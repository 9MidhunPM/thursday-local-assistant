from __future__ import annotations

import json
import logging
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from assistant import server
from assistant.agent.agent import Agent, AgentConfig
from assistant.agent.tasks import TaskStore
from assistant.llm.client import InferenceStats, LlmResponse
from assistant.memory.conversation_store import ConversationStore
from assistant.memory.long_term import LongTermMemory
from assistant.memory.session import SessionMemory
from assistant.security import api_token
from assistant.tools.registry import ToolRegistry


class ControlledLLM:
    provider, model, is_local = "openai", "gpt-6-luna", False

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()

    def chat(self, *_args, **_kwargs):
        self.started.set()
        self.release.wait(10)
        return LlmResponse("Fixture response finished.", [], {}, InferenceStats())

    def chat_stream(self, *args, on_token=None, **kwargs):
        response = self.chat(*args, **kwargs)
        if on_token:
            on_token(response.content)
        return response


def fixture_runtime(path):
    db = path / "fixture.db"
    llm = ControlledLLM()
    loggers = SimpleNamespace(
        **{name: logging.getLogger("qa." + name) for name in ("user", "tool", "model", "error")}
    )
    store = ConversationStore(db)
    memory = LongTermMemory(db)
    agent = Agent(
        llm,
        ToolRegistry(),
        memory,
        SessionMemory(store, 8000),
        loggers,
        AgentConfig(8, "Fixture assistant", 0, False, auto_extract=False),
    )
    agent.tasks = TaskStore(db)
    return SimpleNamespace(
        agent=agent,
        llm=llm,
        loggers=loggers,
        conversation_store=store,
        config=SimpleNamespace(agent=SimpleNamespace(task_timeout_sec=300)),
        tts=None,
        stt=None,
    )


@pytest.fixture
def api(tmp_path, monkeypatch):

    monkeypatch.delenv("THURSDAY_API_TOKEN", raising=False)
    api_token.cache_clear()
    runtime = fixture_runtime(tmp_path)
    monkeypatch.setattr(server, "server_runtime", runtime)
    monkeypatch.setattr(server, "is_busy", False)
    monkeypatch.setattr(server, "active_conversation_id", runtime.agent.start_conversation())
    broadcaster = server.EventBroadcaster()
    monkeypatch.setattr(server, "broadcaster", broadcaster)
    http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.ThursdayHTTPRequestHandler)
    http.daemon_threads = True
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{http.server_port}", timeout=10) as client:
        yield client, runtime
    runtime.llm.release.set()
    http.shutdown()
    http.server_close()
    runtime.agent.close()
    api_token.cache_clear()


def test_busy_request_does_not_switch_conversation_and_cancellation_is_durable(api):
    client, runtime = api
    original = server.active_conversation_id
    other = runtime.conversation_store.create_conversation("Other")
    runtime.llm.release.clear()
    first = client.post(
        "/api/message", json={"prompt": "Fixture work", "conversation_id": original}
    )
    assert first.status_code == 200
    assert runtime.llm.started.wait(2)
    task_id = first.json()["task_id"]
    second = client.post("/api/message", json={"prompt": "Other work", "conversation_id": other})
    assert second.status_code == 409
    assert server.active_conversation_id == runtime.agent.conversation_id == original
    assert client.delete(f"/api/conversations/{original}").status_code == 409
    assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 200
    runtime.llm.release.set()

    deadline = time.monotonic() + 3
    while server.is_busy and time.monotonic() < deadline:
        time.sleep(0.01)
    task = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert task["status"] == "cancelled"
    assert task["conversation_id"] == original
    resumed = client.post(f"/api/tasks/{task_id}/resume")
    assert resumed.status_code == 200
    child = runtime.agent.tasks.get(resumed.json()["task_id"])
    assert child["parent_id"] == task_id
    assert child["conversation_id"] == original


def test_new_endpoints_reject_cross_origin_and_require_configured_token(api, monkeypatch):
    client, _ = api

    assert (
        client.get("/api/memory", headers={"Origin": "https://hostile.example"}).status_code == 403
    )
    assert (
        client.post(
            "/api/message", json={"prompt": "test"}, headers={"Origin": "https://hostile.example"}
        ).status_code
        == 403
    )
    monkeypatch.setenv("THURSDAY_API_TOKEN", "fixture-token")
    api_token.cache_clear()
    assert client.get("/api/tasks").status_code == 401
    assert (
        client.get("/api/tasks", headers={"Authorization": "Bearer fixture-token"}).status_code
        == 200
    )
    assert client.get("/api/events").status_code == 401


def test_memory_review_round_trip_and_invalid_task(api):
    client, runtime = api
    runtime.agent._memory.set_preference("editor", "Kate", "2026-09-30T00:00:00+00:00")
    item = client.get("/api/memory").json()["items"][0]
    assert client.post(f"/api/memory/{item['id']}", json={"value": "VS Code"}).status_code == 200
    assert runtime.agent._memory.get_preference("editor").value == "VS Code"
    current = next(
        item for item in client.get("/api/memory").json()["items"] if item["status"] == "current"
    )
    assert client.post(f"/api/memory/{current['id']}", json={}).status_code == 200
    assert runtime.agent._memory.get_preference("editor") is None
    assert client.post("/api/memory/1/junk", json={}).status_code == 400
    assert client.get("/api/tasks/missing").status_code == 404
    assert (
        client.post("/api/message", json={"prompt": "test", "conversation_id": "bad"}).status_code
        == 400
    )
    assert not server.is_busy


def test_sse_events_identify_task_and_conversation(api):
    _, runtime = api
    events = server.broadcaster.add_client()
    run = runtime.agent.tasks.create("test", server.active_conversation_id)
    server.broadcaster.broadcast("tool_result", {"tool": "fixture", "success": True})
    event = events.get(timeout=1)
    assert event["data"]["task_id"] == run.data["id"]
    assert event["data"]["conversation_id"] == server.active_conversation_id
    assert "fixture" in json.dumps(event)
    run.finish("completed", "done")
