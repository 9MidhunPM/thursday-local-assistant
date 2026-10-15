from __future__ import annotations

import io
import json
import re
import shutil
import stat
import subprocess
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from assistant.native_client import NativeClient, iter_sse
from assistant.web_launch import WebLaunchError


@pytest.fixture
def native_api(tmp_path, monkeypatch):
    requests = []
    events = []

    class OutputObserver:
        def __init__(self, parent_pid, publish):
            self.parent_pid = parent_pid
            self.publish = publish
            self.active = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

    monkeypatch.setattr("assistant.native_client.OutputObserver", OutputObserver)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append((self.path, dict(self.headers), None))
            path = urlsplit(self.path).path
            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/redirect-destination")
                self.end_headers()
                return
            if path.startswith("/api/conversations/"):
                body = json.dumps(
                    {
                        "id": int(path.rsplit("/", 1)[-1]),
                        "messages": [{"role": "user", "content": "Stored fixture message"}],
                    }
                ).encode()
                content_type = "application/json"
            elif path == "/api/events":
                body = b"".join(
                    b"data: " + json.dumps(event).encode() + b"\r\n\r\n" for event in events
                )
                content_type = "text/event-stream"
            elif path.startswith("/api/audio/"):
                body, content_type = b"synthetic-audio", "audio/mpeg"
            else:
                body = b'{"status":"ok","model_ready":true}'
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # Splitting a wire frame must not drop JSON or unicode text.
            for offset in range(0, len(body), 7):
                self.wfile.write(body[offset : offset + 7])
                self.wfile.flush()

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, dict(self.headers), payload))
            self.send_response(200)
            body = b'{"ok":true,"session_id":"voice-fixture","state":"listening"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    received = []
    client = NativeClient(
        f"http://127.0.0.1:{server.server_port}",
        "synthetic-native-token",
        received.append,
        runtime=tmp_path / "runtime",
        settings_path=tmp_path / "config" / "native.json",
    )
    yield client, requests, events, received
    client.close()
    server.shutdown()
    server.server_close()


def test_sse_multiline_crlf_comments_and_interrupted_record():
    wire = (
        b': keepalive\r\n\r\ndata: {"type":"token",\r\n'
        b'data: "data":{"chunk":"hello"}}\r\n\r\n'
        b'data: not-json\n\ndata: {"type":"token","data":{"chunk":"lost"}}'
    )
    assert list(iter_sse(io.BytesIO(wire))) == [{"type": "token", "data": {"chunk": "hello"}}]


def test_authenticated_stream_reconnect_replays_current_state(native_api):
    client, requests, events, received = native_api
    events.extend(
        [
            {"type": "init", "data": {"conversation_id": 9, "history": []}},
            {"type": "token", "data": {"chunk": "Hello — welcome"}},
        ]
    )
    client.stream_once()
    events[:] = [{"type": "init", "data": {"conversation_id": 9, "busy": False}}]
    client.stream_once()
    assert [event["type"] for event in received] == [
        "native_connection",
        "init",
        "token",
        "native_connection",
        "init",
    ]
    assert received[2]["data"]["chunk"] == "Hello — welcome"
    assert received[-1]["data"]["busy"] is False
    for path, headers, _body in requests:
        assert "token" not in path
        assert headers["Authorization"] == "Bearer synthetic-native-token"
        assert "client_id=native" in path
    assert "synthetic-native-token" not in json.dumps(received)


def test_commands_authenticate_and_voice_and_confirm_keep_identifiers(native_api):
    client, requests, _events, received = native_api
    client.command({"command": "message", "prompt": "Fixture message", "conversation_id": 7})
    client.command({"command": "voice_stop", "session_id": "hold-42"})
    client.command({"command": "confirm", "id": "review-1", "approved": True})
    client.command({"command": "task_resume", "task_id": "fixture-task"})
    client.command({"command": "playback", "generation_id": "speech-1", "state": "playing"})
    assert requests[0][2] == {
        "prompt": "Fixture message",
        "conversation_id": 7,
        "client_id": "native",
        "tts": False,
    }
    assert requests[1][2] == {"session_id": "hold-42"}
    assert requests[2][2] == {"id": "review-1", "approved": True}
    assert requests[3][0] == "/api/tasks/fixture-task/resume"
    assert requests[4][2] == {
        "generation_id": "speech-1",
        "state": "playing",
        "client_id": "native",
    }
    assert all(
        headers["Authorization"] == "Bearer synthetic-native-token"
        for _path, headers, _payload in requests
    )
    assert all(event["type"] == "native_command_result" for event in received)


def test_audio_is_targeted_private_and_removed_after_playback(native_api, monkeypatch):
    client, requests, events, received = native_api
    envelope = {"interval_ms": 40, "levels": [0.0, 0.25, 0.0], "duration_ms": 120}
    monkeypatch.setattr("assistant.native_client.audio_levels", lambda _path: envelope)
    data = {
        "client_id": "native",
        "generation_id": "speech-1",
        "sequence": 2,
        "url": "/api/audio/fixture.mp3",
        "task_id": "fixture-task",
    }
    events.extend(
        [
            {"type": "tts_audio", "data": {**data, "client_id": "another-client"}},
            {"type": "tts_audio", "data": data},
        ]
    )
    client.stream_once()
    audio = [event["data"] for event in received if event["type"] == "tts_audio"]
    assert len(audio) == 1
    assert audio[0]["sequence"] == 2
    assert audio[0]["generation_id"] == "speech-1"
    path = client.runtime / audio[0]["audio_id"]
    assert path.read_bytes() == b"synthetic-audio"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert audio[0]["local_url"] == path.as_uri()
    assert audio[0]["audio_levels"] == envelope
    assert client._output_observer.active
    assert len([request for request in requests if request[0].startswith("/api/audio/")]) == 1
    client.command({"command": "audio_release", "audio_id": audio[0]["audio_id"]})
    assert not path.exists()
    assert not client._output_observer.active


def test_output_observation_lasts_until_all_owned_audio_is_released(native_api, monkeypatch):
    client, _requests, _events, received = native_api
    monkeypatch.setattr("assistant.native_client.audio_levels", lambda _path: None)
    data = {"client_id": "native", "url": "/api/audio/fixture.mp3"}
    client._receive_audio(data)
    client._receive_audio(data)
    files = list(client._audio_files)
    assert client._output_observer.active
    client.command({"command": "audio_release", "audio_id": files[0]})
    assert client._output_observer.active
    status = {"known": True, "audible": False, "node_ids": [123], "reason": "volume_zero"}
    client._output_observer.publish(status)
    assert received[-1] == {"type": "native_output", "data": status}
    client.close()
    assert not client._output_observer.active
    assert not client._audio_files


def test_closed_helper_discards_audio_still_being_measured(native_api, monkeypatch):
    client, _requests, _events, received = native_api
    monkeypatch.setattr("assistant.native_client.audio_levels", lambda _path: client.close())
    client._receive_audio({"client_id": "native", "url": "/api/audio/fixture.mp3"})
    assert not any(event["type"] == "tts_audio" for event in received)
    assert not client._audio_files
    assert not client._output_observer.active


def test_native_web_command_reuses_shared_supervised_launcher(native_api, monkeypatch):
    client, _requests, _events, received = native_api
    launches = []

    def launch(url, *, start_service):
        launches.append((url, start_service))
        return {"status": "launch_requested", "reused": True}

    monkeypatch.setattr("assistant.native_client.open_web_app", launch)
    client.command({"command": "open_web"})
    assert launches == [(client.base_url, True)]
    assert received[-1] == {
        "type": "native_command_result",
        "data": {"command": "open_web", "result": {"status": "launch_requested", "reused": True}},
    }


def test_native_web_failure_emits_actionable_error(native_api, monkeypatch):
    client, _requests, _events, received = native_api

    def launch(_url, *, start_service):
        assert start_service
        message = "The existing web window could not be focused."
        raise WebLaunchError(message)

    monkeypatch.setattr("assistant.native_client.open_web_app", launch)
    client.command({"command": "open_web"})
    assert received[-1] == {
        "type": "native_error",
        "data": {"command": "open_web", "message": "The existing web window could not be focused."},
    }


def test_settings_are_private_boolean_preferences_only(native_api):
    client, _requests, _events, received = native_api
    client.command(
        {
            "command": "settings",
            "values": {
                "muted": True,
                "reduced_motion": True,
                "token": "never-save",
                "other": False,
            },
        }
    )
    assert json.loads(client.settings_path.read_text()) == {
        "muted": True,
        "reduced_motion": True,
        "speak_typed": False,
    }
    assert stat.S_IMODE(client.settings_path.stat().st_mode) == 0o600
    assert received[-1]["data"] == {
        "muted": True,
        "reduced_motion": True,
        "speak_typed": False,
    }


def test_typed_speech_requires_explicit_local_preference_and_respects_mute(native_api):
    client, requests, _events, _received = native_api
    client.command({"command": "settings", "values": {"speak_typed": True}})
    client.command({"command": "message", "prompt": "Speak this fixture"})
    assert requests[-1][2]["tts"] is True
    client.command({"command": "settings", "values": {"muted": True}})
    client.command({"command": "message", "prompt": "Silent fixture"})
    assert requests[-1][2]["tts"] is False


def test_conversation_snapshot_carries_request_and_conversation_identity(native_api):
    client, requests, _events, received = native_api
    client.command({"command": "conversation", "conversation_id": 8, "request_id": 17})
    assert requests[0][0] == "/api/conversations/8"
    assert received[-1] == {
        "type": "native_conversation",
        "data": {
            "conversation_id": 8,
            "request_id": 17,
            "messages": [{"role": "user", "content": "Stored fixture message"}],
        },
    }


def test_stream_authentication_failure_emits_actionable_error(native_api, monkeypatch):
    client, _requests, _events, received = native_api

    def unauthorized():
        raise urllib.error.HTTPError(client.base_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(client, "stream_once", unauthorized)
    monkeypatch.setattr(client._stop, "wait", lambda _delay: True)
    client.stream_forever()
    assert received[0]["type"] == "native_error"
    assert received[0]["data"]["command"] == "connection"
    assert "THURSDAY_API_TOKEN" in received[0]["data"]["message"]
    assert received[1]["data"] == {"connected": False, "reason": "unauthorized"}
    assert "synthetic-native-token" not in json.dumps(received)


def test_native_qml_event_handlers_preserve_drafts_and_reject_stale_conversations():
    """Execute the actual QML JavaScript handlers with small view-only doubles."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise native QML JavaScript")
    source = (Path(__file__).resolve().parents[2] / "desktop/quickshell/Thursday.qml").read_text()
    functions = []
    for name in ("handle", "switchConversation", "refreshHistory", "appendMessage"):
        match = re.search(r"    function " + name + r"\([^\n]*\) \{.*?\n    \}", source, re.DOTALL)
        assert match is not None
        functions.append(match[0])
    script = (
        """
const assert = require('node:assert/strict');
let conversationId=1, historyRequest=0, historyNeedsReload=false, historyLoading=false,
    pendingRow=-1, pendingText='', latestAnswer='', task=null, taskActive=false,
    sendPending=false, busy=false, activity='', submittedDraft='', errorText='';
const messages={items:[],clear(){this.items=[]},append(v){this.items.push(v)}};
const composer={text:'Unsent draft',clear(){this.text=''}};
const Qt={callLater:f=>f()}, history={positionViewAtEnd(){}}, dismissTimer={restart(){}};
const send=()=>{}, friendlyActivity=()=>'', stopAudio=()=>{};
"""
        + "\n".join(functions)
        + """
messages.append({speaker:'user',body:'Old conversation'});
handle({type:'user_message',data:{conversation_id:2,client_id:'web',content:'External request'}});
assert.equal(composer.text,'Unsent draft');
assert.equal(conversationId,2);
assert.equal(messages.items.length,1);
assert.equal(messages.items[0].body,'External request');
handle({type:'native_conversation',data:{conversation_id:1,request_id:historyRequest,
    messages:[{role:'user',content:'Stale history'}]}});
assert.equal(messages.items[0].body,'External request');
submittedDraft='Sent draft'; composer.text='New draft';
handle({type:'native_command_result',data:{command:'message',result:{}}});
assert.equal(composer.text,'New draft');
submittedDraft='Sent draft'; composer.text='Sent draft';
handle({type:'native_command_result',data:{command:'message',result:{}}});
assert.equal(composer.text,'');
let voiceApplied=false; const setVoice=()=>voiceApplied=true;
handle({type:'native_command_result',data:{command:'voice_stop',result:{state:'transcribing'}}});
assert.equal(voiceApplied,false);
busy=false;
handle({type:'native_conversation',data:{conversation_id:2,request_id:historyRequest,
    messages:[{role:'user',content:'Correct history'}]}});
assert.equal(messages.items[0].body,'Correct history');
"""
    )
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)


def test_invalid_native_commands_cannot_change_the_request_target(native_api):
    client, requests, _events, received = native_api
    client.command({"command": "task_cancel", "task_id": "../shutdown"})
    client.command({"command": "run_shell", "value": "untrusted"})
    client.command({"command": "audio_release", "audio_id": "../../unrelated"})
    assert not requests
    assert [event["type"] for event in received] == ["native_error", "native_error"]


def test_transport_never_follows_authenticated_redirects(native_api):
    client, requests, _events, _received = native_api
    with pytest.raises(urllib.error.HTTPError, match="302"):
        client.request("/redirect")
    assert len(requests) == 1
    assert requests[0][0] == "/redirect"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org",
        "http://example.org",
        "http://user@localhost",
        "http://localhost?token=x",
    ],
)
def test_native_transport_rejects_remote_or_credential_urls(tmp_path, url):
    with pytest.raises(ValueError, match="loopback"):
        NativeClient(url, None, lambda _event: None, runtime=tmp_path)
