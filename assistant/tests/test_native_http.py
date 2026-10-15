"""Exercise native controls against the real local HTTP/task/voice seams."""

from __future__ import annotations

import io
import threading
import time
import wave
from types import SimpleNamespace

import httpx
import pytest

from assistant import server
from assistant.agent.confirm import ConfirmBroker
from assistant.paths import private_write, runtime_dir
from assistant.security import api_token
from assistant.tests.test_task_http import fixture_runtime


class FixtureRecorder:
    def start(self):
        pass

    def stop(self):
        pass

    def wav_bytes(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0" * 16000)
        return output.getvalue()


@pytest.fixture
def native_api(tmp_path, monkeypatch):
    monkeypatch.setenv("THURSDAY_API_TOKEN", "fixture-native-access")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    api_token.cache_clear()
    runtime = fixture_runtime(tmp_path)
    runtime.stt = SimpleNamespace(
        transcribe_bytes=lambda *_args, **_kwargs: "Fixture voice request"
    )
    monkeypatch.setattr(server, "server_runtime", runtime)
    monkeypatch.setattr(server, "voice_controller", None)
    monkeypatch.setattr(server, "_voice_runtime", None)
    monkeypatch.setattr(server, "is_busy", False)
    monkeypatch.setattr(server, "active_conversation_id", runtime.agent.start_conversation())
    monkeypatch.setattr(server, "broadcaster", server.EventBroadcaster())
    broker = ConfirmBroker()
    broker.set_broadcast(server.broadcaster.broadcast)
    monkeypatch.setattr(server, "confirm_broker", broker)
    controller = server._voice()
    controller._recorder_factory = FixtureRecorder
    http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.ThursdayHTTPRequestHandler)
    http.daemon_threads = True
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{http.server_port}",
        headers={"Authorization": "Bearer fixture-native-access"},
        timeout=5,
    ) as client:
        yield client, runtime, controller, broker
    runtime.llm.release.set()
    controller.close()
    http.shutdown()
    http.server_close()
    runtime.agent.close()
    api_token.cache_clear()


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_voice_http_auth_origin_and_shared_task_submission(native_api):
    client, runtime, controller, _broker = native_api
    assert (
        client.post("/api/voice/start", headers={"Authorization": ""}, json={}).status_code == 401
    )
    assert (
        client.post(
            "/api/voice/start", headers={"Origin": "https://untrusted.example"}, json={}
        ).status_code
        == 403
    )
    started = client.post("/api/voice/start", json={}).json()
    assert started["state"] == "listening"
    assert client.get("/api/voice").json()["session_id"] == started["session_id"]
    stopped = client.post("/api/voice/stop", json={"session_id": started["session_id"]})
    assert stopped.status_code == 200
    eventually(lambda: controller.snapshot()["state"] == "submitted")
    task = runtime.agent.tasks.get(controller.snapshot()["task_id"])
    assert task["goal"] == "Fixture voice request"
    eventually(lambda: not server.is_busy)


def test_barge_in_cancels_old_task_and_stale_voice_submission(native_api):
    client, runtime, controller, _broker = native_api
    runtime.llm.release.clear()
    task_id = client.post("/api/message", json={"prompt": "Controlled work"}).json()["task_id"]
    assert runtime.llm.started.wait(2)
    started = client.post("/api/voice/start", json={}).json()
    assert runtime.agent.tasks.active.cancelled.is_set()
    client.post("/api/voice/stop", json={"session_id": started["session_id"]})
    client.post("/api/voice/cancel", json={"session_id": started["session_id"]})
    assert controller.snapshot()["state"] == "cancelled"
    runtime.llm.release.set()
    eventually(lambda: not server.is_busy)
    assert runtime.agent.tasks.latest()["id"] == task_id
    assert runtime.agent.tasks.get(task_id)["status"] == "cancelled"


def test_playback_reports_real_owner_and_rejects_stale_generations(native_api, monkeypatch):
    client, _runtime, _controller, _broker = native_api
    monkeypatch.setattr(server, "playback_generation", "current-generation")
    monkeypatch.setattr(server, "playback_client", "native")
    monkeypatch.setattr(server, "tts_active", False)
    payload = {"client_id": "native", "generation_id": "current-generation", "state": "playing"}
    assert client.post("/api/playback", json=payload).status_code == 200
    assert client.get("/health").json()["tts_active"] is True
    assert client.post("/api/playback", json={**payload, "client_id": "other"}).status_code == 409
    assert client.post("/api/playback", json={**payload, "generation_id": "old"}).status_code == 409
    assert client.post("/api/playback", json={**payload, "state": "idle"}).status_code == 200
    assert server.tts_active is False


@pytest.mark.parametrize(
    "event_type", ["tts_audio", "tts_status", "tts_preparing", "tts_synthesis_done"],
)
def test_audio_routes_to_one_client_even_with_web_clients(event_type):
    broadcaster = server.EventBroadcaster()
    native = broadcaster.add_client("quickshell", "native")
    web = broadcaster.add_client("web", "tab-one")
    second = broadcaster.add_client("web", "tab-two")
    broadcaster.broadcast(event_type, {"client_id": "native", "url": "/api/audio/fixture"})
    assert native.get_nowait()["type"] == event_type
    assert web.empty()
    broadcaster.broadcast(event_type, {"client_id": "tab-two", "url": "/api/audio/fixture"})
    assert second.get_nowait()["type"] == event_type
    assert native.empty()
    assert web.empty()


def test_confirmation_snapshot_is_actionable_once(native_api):
    client, _runtime, _controller, broker = native_api
    result = []
    thread = threading.Thread(
        target=lambda: result.append(broker.request("Fixture approval", 2, force_web=True))
    )
    thread.start()
    eventually(lambda: bool(broker.snapshot()))
    pending = broker.snapshot()[0]
    assert pending["prompt"] == "Fixture approval"
    assert (
        client.post("/api/confirm", json={"id": pending["id"], "approved": True}).status_code == 200
    )
    assert (
        client.post("/api/confirm", json={"id": pending["id"], "approved": True}).status_code == 404
    )
    thread.join(2)
    assert result == [True]
    assert broker.snapshot() == []


def test_private_audio_requires_auth_and_refuses_links(native_api, tmp_path):
    client, _runtime, _controller, _broker = native_api
    path = runtime_dir("tts") / "tts_12345678.mp3"
    private_write(path, b"fixture audio")
    assert (
        client.get("/api/audio/tts_12345678.mp3", headers={"Authorization": ""}).status_code == 401
    )
    assert client.get("/api/audio/tts_12345678.mp3").content == b"fixture audio"
    assert not path.exists()
    other = tmp_path / "private.txt"
    other.write_text("Private fixture")
    path.symlink_to(other)
    assert client.get("/api/audio/tts_12345678.mp3").status_code == 404


def test_browser_exchange_authenticates_cookie_sse_and_preserves_origin_guards(native_api):
    client, _runtime, _controller, _broker = native_api
    with httpx.Client(base_url=client.base_url, timeout=5) as browser:
        assert browser.get("/api/browser-session").status_code == 401
        rejected = browser.post("/api/browser-session", headers={"Authorization": "Bearer wrong"})
        assert rejected.status_code == 401
        approved = browser.post(
            "/api/browser-session", headers={"Authorization": "Bearer fixture-native-access"}
        )
        assert approved.status_code == 200
        cookie = approved.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Path=/api" in cookie
        assert "fixture-native-access" not in cookie
        assert browser.get("/api/browser-session").status_code == 200
        assert browser.get("/api/voice").status_code == 200
        with browser.stream("GET", "/api/events?client_id=fixture-browser") as stream:
            assert stream.status_code == 200
            first = next(stream.iter_lines())
            assert '"type": "init"' in first
        # A signed session grants API use, but renewal asks for the operator
        # token again. Cross-origin callers cannot mint a browser session.
        assert browser.post("/api/browser-session").status_code == 401
        assert (
            browser.post(
                "/api/browser-session",
                headers={
                    "Authorization": "Bearer fixture-native-access",
                    "Origin": "https://untrusted.example",
                },
            ).status_code
            == 403
        )
