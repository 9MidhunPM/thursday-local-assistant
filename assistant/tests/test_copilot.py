from __future__ import annotations

# Several valid policy layers may reject the same intentionally unsafe fixture.
# ruff: noqa: PT011
import asyncio
import base64
import copy
import json
import logging
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest

from assistant.agent.agent import Agent, AgentConfig
from assistant.agent.confirm import ConfirmBroker
from assistant.agent.tasks import TaskStore
from assistant.integrations.hypruse import CaptureCache, HypruseBridge, HypruseTool
from assistant.llm.client import ChatMessage, InferenceStats, LlmResponse, ToolCall
from assistant.llm.responses import ResponseIncomplete, ResponsesClient
from assistant.memory.auto_extract import _store
from assistant.memory.long_term import LongTermMemory
from assistant.memory.short_term import Message, ShortTermMemory
from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.registry import ToolRegistry


def response_client(handler):
    client = ResponsesClient(
        "https://api.openai.com/v1", "gpt-6-luna", 0.2, 4096, 10, None, provider="openai"
    )
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def complete(output):
    return {
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 30,
            "output_tokens": 10,
            "total_tokens": 40,
            "input_tokens_details": {"cached_tokens": 5},
            "output_tokens_details": {"reasoning_tokens": 4},
        },
    }


def test_responses_replays_reasoning_and_maps_images():
    output = [
        {"type": "reasoning", "id": "r1", "encrypted_content": "opaque"},
        {
            "type": "function_call",
            "name": "calculate",
            "call_id": "c1",
            "arguments": '{"expression":"2+2"}',
        },
    ]
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=complete(output))

    client = response_client(handler)
    first = client.chat(
        [ChatMessage("user", "calculate")],
        tools=[
            {
                "type": "function",
                "function": {"name": "calculate", "parameters": {"type": "object"}},
            }
        ],
    )
    client.chat(
        [
            ChatMessage("assistant", None, response_items=first.raw["output"]),
            ChatMessage("tool", "4", tool_call_id="c1"),
            ChatMessage(
                "user", [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aA=="}}]
            ),
        ]
    )
    assert seen[0]["store"] is False
    assert "temperature" not in seen[0]
    assert seen[0]["reasoning"]["effort"] == "low"
    assert seen[1]["input"][:2] == output
    assert seen[1]["input"][2]["type"] == "function_call_output"
    assert seen[1]["input"][3]["content"][0]["type"] == "input_image"
    assert first.stats.cached_tokens == 5
    assert first.stats.reasoning_tokens == 4
    client.close()


def test_partial_and_malformed_calls_cannot_execute():
    client = response_client(
        lambda _request: httpx.Response(200, json={"status": "incomplete", "output": []})
    )
    with pytest.raises(ResponseIncomplete):
        client.chat([ChatMessage("user", "hello")])
    with pytest.raises(ValueError):
        client._parse_responses(
            complete([{"type": "function_call", "call_id": "c", "name": "x", "arguments": "[]"}])
        )
    client.close()


def test_stream_requires_completed_event_and_records_usage():
    final = complete(
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hi"}],
            }
        ]
    )
    events = [
        {"type": "response.output_text.delta", "delta": "Hi"},
        {"type": "response.completed", "response": final},
    ]
    client = response_client(
        lambda _request: httpx.Response(
            200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
        )
    )
    tokens = []
    result = client.chat_stream([ChatMessage("user", "hello")], on_token=tokens.append)
    assert tokens == ["Hi"]
    assert result.stats.total_tokens == 40
    events.pop()
    with pytest.raises(ResponseIncomplete):
        client.chat_stream([ChatMessage("user", "hello")])
    client.close()


def test_checkpoint_cancel_and_restart(tmp_path):
    store = TaskStore(tmp_path / "db")
    task = store.create("goal", 1)
    task.outcome({"tool": "write_file", "success": True, "_images": ["private"]}, {"path": "x"})
    assert "_images" not in store.get(task.data["id"])["outcomes"][0]
    assert store.cancel(task.data["id"])
    with pytest.raises(InterruptedError):
        task.check()
    reopened = TaskStore(tmp_path / "db")
    assert reopened.get(task.data["id"])["status"] == "interrupted"
    task.finish("cancelled", "Stopped")
    next_run = store.create("goal", 1, parent=task.data["id"])
    assert next_run.data["parent_id"] == task.data["id"]
    next_run.account(InferenceStats(prompt_tokens=100, completion_tokens=10), "gpt-6-luna")
    assert store.get(next_run.data["id"])["usage"]["prompt_tokens"] == 100


def test_memory_requires_user_evidence_and_corrections_remove_old_value(tmp_path):
    memory = LongTermMemory(tmp_path / "db")
    fake = {"preferences": [{"key": "editor", "value": "VS Code", "evidence": "I use VS Code"}]}
    assert _store(fake, memory, "Hello", 1, 1) == 0
    assert _store(fake, memory, "I use VS Code", 1, 2) == 1
    row = memory.list_assertions()[0]
    assert row["message_id"] == 2
    memory.modify_assertion(row["id"], "Kate")
    assert memory.get_preference("editor").value == "Kate"
    statuses = {r["status"] for r in memory.list_assertions()}
    assert statuses == {"current", "superseded"}
    current = next(r for r in memory.list_assertions() if r["status"] == "current")
    memory.modify_assertion(current["id"])
    assert memory.get_preference("editor") is None


def test_capture_expiry_and_size():
    cache = CaptureCache()
    url = cache.add(base64.b64encode(b"image").decode(), "image/png")
    assert cache.get(url.rsplit("/", 1)[1]) == (b"image", "image/png")
    cache.clear()
    assert cache.get(url.rsplit("/", 1)[1]) is None
    with pytest.raises(ValueError):
        cache.add("!!!", "image/png")


def test_desktop_requires_fresh_target_and_cannot_override_auth():
    bridge = SimpleNamespace(
        snapshot={"windows": [{"address": "0x1"}]}, snapshot_at=time.monotonic(), call=Mock()
    )
    tool = HypruseTool(bridge, "keyboard")
    context = SimpleNamespace(confirm=Mock(return_value=True))
    with pytest.raises(ValueError):
        tool._validate("keyboard", {"action": "type", "text": "hello"}, context)
    with pytest.raises(ValueError):
        tool._validate("keyboard", {"action": "type", "window": "stale"}, context)
    with pytest.raises(ValueError):
        tool._validate("keyboard", {"action": "type", "window": "0x1", "allow_auth": True}, context)
    bridge.snapshot_at = 0
    with pytest.raises(ValueError):
        tool._validate("keyboard", {"action": "type", "window": "0x1"}, context)


def test_nested_sequence_and_launch_cannot_bypass_policy():
    tool = HypruseTool(SimpleNamespace(snapshot={}, snapshot_at=0))
    context = SimpleNamespace(confirm=Mock(return_value=False))
    for args in (
        {"command": "kitty bash -c rm"},
        {"command": "brave; echo test"},
        {"command": "bash -c ls"},
        {"command": "code --install-extension untrusted.extension"},
        {"command": "firefox --remote-debugging-port=9999"},
    ):
        with pytest.raises(ValueError):
            tool._validate("launch", args, context)
    with pytest.raises(ValueError):
        tool._validate("sequence", {"steps": [{"op": "keyboard", "allow_auth": True}]}, context)
    tool.bridge.snapshot = {"windows": []}
    tool.bridge.snapshot_at = time.monotonic()
    with pytest.raises(PermissionError):
        tool._validate("use_bind", {"combo": "SUPER+X"}, context)


def test_desktop_launch_accepts_basic_window_flags():
    tool = HypruseTool(SimpleNamespace(snapshot={}, snapshot_at=0))
    tool._validate(
        "launch", {"command": "firefox --new-window https://example.com"}, SimpleNamespace()
    )


def approval_bridge():
    snapshot = {
        "windows": [
            {"address": "0x1", "class": "test", "pid": 42, "at": [0, 0], "size": [500, 200]}
        ],
        "active_window": "0x1",
        "cursor": [100, 100],
    }
    current = copy.deepcopy(snapshot)
    bridge = SimpleNamespace(snapshot=snapshot, snapshot_at=time.monotonic())

    def call(name, _args):
        if name == "desktop":
            return {"structuredContent": copy.deepcopy(current), "content": []}
        return {"structuredContent": {"delivered": name}, "content": []}

    bridge.call = Mock(side_effect=call)
    return bridge, current


def test_approval_cursor_focus_and_delay_rearm_before_one_keypress():
    bridge, current = approval_bridge()

    def approve(_prompt):
        current.update(cursor=[900, 800], active_window="approval-window")
        bridge.snapshot_at -= 60  # Time spent reading the approval is expected.
        return True

    context = SimpleNamespace(confirm=Mock(side_effect=approve))
    result = HypruseTool(bridge, "keyboard").execute(
        {"action": "key", "keys": "enter", "window": "0x1"}, context
    )
    assert result["success"]
    assert [call.args[0] for call in bridge.call.call_args_list] == ["desktop", "keyboard"]
    assert bridge.call.call_args_list[1].args[1]["window"] == "0x1"
    assert bridge.snapshot["cursor"] == [900, 800]
    context.confirm.assert_called_once()


@pytest.mark.parametrize("change", ["closed", "reused_address", "read_failed"])
def test_approval_handoff_cannot_deliver_to_changed_target(change):
    bridge, current = approval_bridge()

    def approve(_prompt):
        if change == "closed":
            current["windows"] = []
        elif change == "reused_address":
            current["windows"][0]["pid"] = 999
        else:
            bridge.call.side_effect = None
            bridge.call.return_value = {"isError": True, "content": [{"text": "read failed"}]}
        return True

    result = HypruseTool(bridge, "keyboard").execute(
        {"action": "key", "keys": "enter", "window": "0x1"},
        SimpleNamespace(confirm=Mock(side_effect=approve)),
    )
    assert result["success"] is False
    assert result["pause"]
    assert [call.args[0] for call in bridge.call.call_args_list] == ["desktop"]


def test_declining_approval_does_not_refresh_or_execute():
    bridge, _current = approval_bridge()
    with pytest.raises(PermissionError):
        HypruseTool(bridge, "keyboard").execute(
            {"action": "key", "keys": "enter", "window": "0x1"},
            SimpleNamespace(confirm=Mock(return_value=False)),
        )
    bridge.call.assert_not_called()


def test_approved_pointer_keeps_original_coordinates():
    bridge, current = approval_bridge()
    args = {"action": "click"}

    def approve(_prompt):
        current["cursor"] = [900, 800]
        return True

    result = HypruseTool(bridge, "pointer").execute(
        args, SimpleNamespace(confirm=Mock(side_effect=approve))
    )
    assert result["success"]
    assert bridge.call.call_args_list[-1].args[1]["x"] == 100
    assert bridge.call.call_args_list[-1].args[1]["y"] == 100
    assert args == {"action": "click"}


def test_approved_sequence_refreshes_once_without_requesting_approval_again():
    bridge, _current = approval_bridge()
    context = SimpleNamespace(confirm=Mock(return_value=True))
    result = HypruseTool(bridge, "sequence").execute(
        {
            "steps": [
                {"op": "keyboard", "action": "key", "keys": "enter", "window": "0x1"},
                {"op": "keyboard", "action": "key", "keys": "delete", "window": "0x1"},
            ]
        },
        context,
    )
    assert result["success"]
    assert context.confirm.call_count == 2
    assert [call.args[0] for call in bridge.call.call_args_list] == ["desktop", "sequence"]


class RecordingTool(BaseTool):
    def __init__(self, name, calls):
        self.metadata = ToolMetadata(
            name,
            "test mutation",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        self.calls = calls

    def execute(self, _args, _context):
        self.calls.append(self.name)
        return {"success": True}


def make_agent(tmp_path, responses, max_steps=5):
    registry = ToolRegistry()
    calls = []
    for name in ["mutation_one", "mutation_two"]:
        registry.register(RecordingTool(name, calls))
    llm = SimpleNamespace(
        chat=Mock(side_effect=responses), model="gpt-6-luna", provider="openai", is_local=False
    )
    loggers = SimpleNamespace(
        **{name: logging.getLogger("test." + name) for name in ["user", "model", "tool", "error"]}
    )
    agent = Agent(
        llm,
        registry,
        LongTermMemory(tmp_path / "db"),
        ShortTermMemory(max_tokens=8000),
        loggers,
        AgentConfig(max_steps, "test", 0, False, auto_extract=False),
    )
    agent.tasks = TaskStore(tmp_path / "db")
    return agent, calls


def test_mutations_are_serial_and_budget_pause_is_resumable(tmp_path):
    response = LlmResponse(
        None,
        [ToolCall("a", "mutation_one", {}), ToolCall("b", "mutation_two", {})],
        {},
        InferenceStats(),
    )
    agent, calls = make_agent(tmp_path, [response], max_steps=1)
    answer = agent.handle_message("do work")
    assert calls == ["mutation_one", "mutation_two"]
    assert "budget" in answer
    assert agent.tasks.latest()["status"] == "paused"
    assert len(agent.tasks.latest()["outcomes"]) == 2
    agent.close()


def test_invalid_arguments_do_not_reach_tool(tmp_path):
    agent, calls = make_agent(tmp_path, [])
    result = agent._execute_tool("mutation_one", {"surprise": "value"})
    assert result["success"] is False
    assert not calls
    agent.close()


def test_large_image_uses_image_allowance_and_retains_request(tmp_path):
    agent, _ = make_agent(tmp_path, [LlmResponse("Verified", [], {}, InferenceStats())])
    user_message = Message("user", "Read the owned test window")
    agent._short_term.add(user_message)
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * 400000}}
    agent._turn_images = [image]
    agent._request_llm("Read the owned test window", None, tools=[])
    messages = agent._llm.chat.call_args.args[0]
    assert any(message.content == user_message.content for message in messages)
    assert messages[-1].content[-1] == image
    assert Agent._estimate_tokens([image]) == 4100
    agent.close()


def test_completed_response_usage_survives_cancel_before_tools(tmp_path):
    agent, calls = make_agent(tmp_path, [])
    task = agent.tasks.create("do work", None)
    agent._task = task

    def cancelled_response(*_args, **_kwargs):
        task.cancelled.set()
        return LlmResponse(
            None,
            [ToolCall("a", "mutation_one", {})],
            {},
            InferenceStats(prompt_tokens=100, completion_tokens=10),
        )

    agent._llm.chat.side_effect = cancelled_response
    with pytest.raises(InterruptedError):
        agent._request_llm("do work", None, tools=[])
    assert agent.tasks.get(task.data["id"])["usage"]["prompt_tokens"] == 100
    assert not calls
    agent.close()


def test_discovery_extends_tools_for_later_steps(tmp_path):
    response = LlmResponse(
        None, [ToolCall("d", "discover_tools", {"query": "mutation"})], {}, InferenceStats()
    )
    final = LlmResponse("Done", [], {}, InferenceStats())
    agent, _ = make_agent(tmp_path, [response, final])
    agent.handle_message("hello")
    assert "mutation_one" in agent._discovered
    assert len(agent._llm.chat.call_args_list) == 2
    agent.close()


def test_cancellation_unblocks_confirmation():

    broker = ConfirmBroker()
    cancel = threading.Event()
    broker.set_broadcast(lambda kind, _data: cancel.set() if kind == "confirm_required" else None)
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "sys.stdin.isatty", return_value=False
    ):
        assert broker.request("test", timeout_sec=10, cancel_event=cancel) is False
    assert not broker._pending


def test_queued_extraction_cannot_restore_forgotten_memory(tmp_path):
    memory = LongTermMemory(tmp_path / "db")
    revision = memory.revision
    memory.forget_everything()
    data = {"preferences": [{"key": "editor", "value": "Kate", "evidence": "I use Kate"}]}
    assert _store(data, memory, "I use Kate", 1, 1, revision) == 0
    assert not list(memory.list_preferences())


def test_legacy_memory_review_preserves_unknown_provenance(tmp_path):
    memory = LongTermMemory(tmp_path / "db")
    memory.set_preference("editor", "Kate", "2026-09-30T00:00:00+00:00")
    memory.store_memory("routine", "Use the editor", "2026-09-30T00:00:00+00:00")
    rows = memory.list_assertions()
    assert len(rows) == len(memory.list_assertions()) == 2
    assert all("source unavailable" in row["evidence"] for row in rows)
    routine = next(row for row in rows if row["kind"] == "memories")
    assert memory.modify_assertion(routine["id"], "Open Kate")
    assert memory.recall_memory("routine").content == "Open Kate"
    current = next(
        row
        for row in memory.list_assertions()
        if row["kind"] == "memories" and row["status"] == "current"
    )
    assert memory.modify_assertion(current["id"])
    assert memory.recall_memory("routine") is None


def test_desktop_nested_then_refreshes_snapshot():
    bridge = SimpleNamespace(
        snapshot=None,
        snapshot_at=0,
        call=Mock(return_value={"structuredContent": {"after": {"windows": []}}, "content": []}),
    )
    tool = HypruseTool(bridge, "desktop")
    assert tool.execute({}, SimpleNamespace())["success"]
    assert bridge.snapshot == {"windows": []}
    assert bridge.snapshot_at > 0


def test_first_desktop_observation_keeps_new_verbs_available(tmp_path):
    agent, _ = make_agent(tmp_path, [])
    bridge = SimpleNamespace(
        snapshot=None,
        snapshot_at=0,
        call=Mock(return_value={"structuredContent": {"windows": []}, "content": []}),
    )
    agent._tool_registry.register(HypruseTool(bridge, "desktop"))
    agent._tool_registry.register(HypruseTool(bridge, "keyboard"))
    assert agent._execute_tool("hypruse__desktop", {})["success"]
    assert "hypruse__keyboard" in agent._discovered
    agent.close()


def test_busy_direct_call_cannot_replace_active_task(tmp_path):
    agent, _ = make_agent(tmp_path, [])
    current = agent.tasks.create("first", None)
    agent._task = current
    agent._run_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already handling"):
            agent.handle_message("second")
        assert agent._task is current
    finally:
        agent._run_lock.release()
        current.finish("completed", "test finished")
        agent.close()


def test_structured_capture_bytes_never_enter_tool_history():
    raw = {
        "structuredContent": {
            "result": [{"type": "image", "data": "PRIVATE_BYTES", "mimeType": "image/png"}]
        },
        "content": [],
    }
    bridge = SimpleNamespace(snapshot=None, snapshot_at=0, call=Mock(return_value=raw))
    result = HypruseTool(bridge, "screenshot").execute({}, SimpleNamespace())
    assert "PRIVATE_BYTES" not in json.dumps(result)


def test_stop_closes_idle_model_stream_before_any_tool_can_execute():
    class IdleStream(httpx.SyncByteStream):
        def __init__(self):
            self.started = threading.Event()
            self.closed = threading.Event()

        def __iter__(self):
            self.started.set()
            yield b'data: {"type":"response.created"}\n\n'
            self.closed.wait(2)

        def close(self):
            self.closed.set()

    stream = IdleStream()
    client = response_client(lambda _request: httpx.Response(200, stream=stream))
    cancel = threading.Event()

    def stop():
        assert stream.started.wait(1)
        cancel.set()

    worker = threading.Thread(target=stop)
    worker.start()
    start = time.monotonic()
    with pytest.raises(InterruptedError):
        client.chat_stream([ChatMessage("user", "test")], cancel_event=cancel)
    assert stream.closed.is_set()
    assert time.monotonic() - start < 1
    worker.join()
    client.close()


def test_cancel_between_mutations_keeps_delivered_checkpoint(tmp_path):
    response = LlmResponse(
        None,
        [ToolCall("a", "mutation_one", {}), ToolCall("b", "mutation_two", {})],
        {},
        InferenceStats(),
    )
    agent, calls = make_agent(tmp_path, [response])
    first = agent._tool_registry.get("mutation_one")
    execute = first.execute

    def cancel_after_delivery(args, context):
        result = execute(args, context)
        agent._task.cancelled.set()
        return result

    first.execute = cancel_after_delivery
    agent.handle_message("do work")
    assert calls == ["mutation_one"]
    task = agent.tasks.latest()
    assert task["status"] == "cancelled"
    assert [outcome["tool"] for outcome in task["outcomes"]] == ["mutation_one"]
    agent.close()


@pytest.mark.parametrize("via_review", [True, False])
def test_forgetting_corrected_fact_removes_ancestors_but_keeps_other_facts(tmp_path, via_review):
    memory = LongTermMemory(tmp_path / "db")
    memory.record_assertion(
        "facts",
        {"subject": "user", "predicate": "likes", "object": "Coffee"},
        "I like Coffee",
        1,
        1,
        "2026-09-30",
    )
    memory.record_assertion(
        "facts",
        {"subject": "user", "predicate": "likes", "object": "Books"},
        "I like Books",
        1,
        2,
        "2026-09-30",
    )
    coffee = next(row for row in memory.list_assertions() if row["payload"]["object"] == "Coffee")
    memory.modify_assertion(coffee["id"], "Tea")
    tea = next(row for row in memory.list_assertions() if row["payload"]["object"] == "Tea")
    if via_review:
        memory.modify_assertion(tea["id"])
    else:
        memory.delete_fact("user", "likes", "Tea")
    assert [row["payload"]["object"] for row in memory.list_assertions()] == ["Books"]


def test_large_inline_mcp_image_survives_stdio_framing():
    pytest.importorskip("mcp")

    # A real subprocess sends one MCP frame larger than the default 64 KiB.
    program = """import sys,json,base64
for line in sys.stdin:
    message=json.loads(line)
    if "id" not in message: continue
    method=message["method"]
    if method=="initialize":
        result={"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"fixture","version":"1"}}
    elif method=="tools/list":
        result={"tools":[{"name":"screenshot","description":"test","inputSchema":{"type":"object","properties":{}}}]}
    else:
        result={"content":[{"type":"image","mimeType":"image/png",
                "data":base64.b64encode(b"x"*100000).decode()},
                {"type":"text","text":"geometry preserved"}],"isError":False}
    print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":result}),flush=True)
"""
    create = asyncio.create_subprocess_exec

    async def fixture_process(*_args, **kwargs):
        return await create(sys.executable, "-c", program, **kwargs)

    registry = ToolRegistry()
    bridge = HypruseBridge(registry)
    try:
        with patch("asyncio.create_subprocess_exec", fixture_process):
            bridge.connect()
            result = registry.get("hypruse__screenshot").execute({}, SimpleNamespace())
            assert result["success"]
            assert len(result["_images"][0]["image_url"]["url"]) > 64_000
    finally:
        bridge.close()
