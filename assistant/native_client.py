"""Private, reconnecting JSON-lines transport for the Quickshell companion.

QML never receives credentials. This process loads local configuration, authenticates
HTTP requests, and publishes only normalized events and private audio file URLs.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from assistant.paths import private_dir, private_write, purge_runtime_files, runtime_dir
from assistant.voice.audio import audio_levels
from assistant.voice.output import OutputObserver
from assistant.web_launch import WebLaunchError, open_web_app

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

CLIENT_ID = "native"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_AUDIO_BYTES = 16 * 1024 * 1024


def _invalid(message: str) -> None:
    raise ValueError(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        # Never forward the local control token to a redirect destination.
        return None


def iter_sse(lines: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    """Decode complete SSE records, including multiline data and CRLF streams."""
    data: list[str] = []
    size = 0
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                try:
                    event = json.loads("\n".join(data))
                    if isinstance(event, dict) and isinstance(event.get("type"), str):
                        yield event
                except (ValueError, TypeError):
                    pass
            data = []
            size = 0
        elif line.startswith("data:"):
            value = line[5:].removeprefix(" ")
            size += len(value)
            if size > 2 * 1024 * 1024:
                _invalid("Event exceeded the native transport limit")
            data.append(value)
    # An interrupted partial record must never become an event.


class NativeClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        emit: Callable[[dict[str, Any]], None],
        *,
        runtime: Path | None = None,
        settings_path: Path | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            _invalid("The native companion requires a loopback HTTP address")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._emit_callback = emit
        self._emit_lock = threading.Lock()
        self._stop = threading.Event()
        self.runtime = private_dir(runtime) if runtime else runtime_dir("native")
        purge_runtime_files(self.runtime, older_than=300)
        self.settings_path = settings_path or (
            Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "thursday"
            / "native.json"
        )
        self.settings = {"muted": False, "reduced_motion": False, "speak_typed": False}
        try:
            values = json.loads(self.settings_path.read_text())
            if isinstance(values, dict):
                self.settings.update(
                    {k: v for k, v in values.items() if k in self.settings and isinstance(v, bool)}
                )
        except (OSError, ValueError):
            pass
        self._audio_files: dict[str, Path] = {}
        self._audio_lock = threading.RLock()
        self._output_observer = OutputObserver(
            os.getppid(), lambda status: self.emit("native_output", status),
        )
        # Do not use desktop proxy variables for a privileged loopback transport.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def emit(self, event_type: str, data: Any) -> None:
        with self._emit_lock:
            self._emit_callback({"type": event_type, "data": data})

    def request(
        self, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 12
    ) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            _invalid("Invalid local API path")
        headers = {"Accept": "application/json", "User-Agent": "Thursday-Native/1"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers)
        return self._opener.open(req, timeout=timeout)

    def json_request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        with self.request(path, payload) as response:
            raw = response.read(2 * 1024 * 1024)
        return json.loads(raw) if raw else {}

    def health(self) -> None:
        try:
            self.emit("native_health", self.json_request("/health"))
        except (OSError, ValueError, http.client.HTTPException):
            self.emit("native_health", {"status": "offline", "model_ready": False})

    def _receive_audio(self, data: dict[str, Any]) -> None:
        if data.get("client_id") != CLIENT_ID:
            return
        if self.settings["muted"]:
            with contextlib.suppress(OSError):
                self.json_request(
                    "/api/playback",
                    {
                        "client_id": CLIENT_ID,
                        "generation_id": data.get("generation_id"),
                        "state": "idle",
                    },
                )
            return
        url = data.get("url", "")
        if not isinstance(url, str) or not re.fullmatch(r"/api/audio/[\w.-]+", url):
            _invalid("Invalid audio reference")
        with self.request(url) as response:
            content = response.read(MAX_AUDIO_BYTES + 1)
        if len(content) > MAX_AUDIO_BYTES:
            _invalid("Audio exceeded the native transport limit")
        name = uuid.uuid4().hex + ".mp3"
        target = self.runtime / name
        private_write(target, content)
        with self._audio_lock:
            if self._stop.is_set():
                target.unlink(missing_ok=True)
                return
            self._audio_files[name] = target
            self._output_observer.start()
        audio = {**data, "local_url": target.as_uri(), "audio_id": name}
        envelope = audio_levels(target)
        if envelope is not None:
            audio["audio_levels"] = envelope
        with self._audio_lock:
            if self._stop.is_set() or name not in self._audio_files:
                return
            self.emit("tts_audio", audio)
        self._expire_audio()

    def _expire_audio(self) -> None:
        cutoff = time.time() - 300
        with self._audio_lock:
            for name, path in list(self._audio_files.items()):
                with contextlib.suppress(OSError):
                    if not path.exists() or path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                        self._audio_files.pop(name, None)
            if not self._audio_files:
                self._output_observer.stop()

    def stream_once(self) -> None:
        with self.request("/api/events?client=quickshell&client_id=native", timeout=35) as response:
            self.emit("native_connection", {"connected": True})
            for event in iter_sse(response):
                if self._stop.is_set():
                    return
                kind, data = event["type"], event.get("data", {})
                if not isinstance(data, dict):
                    continue
                if kind == "tts_audio":
                    try:
                        self._receive_audio(data)
                    except (OSError, ValueError, http.client.HTTPException):
                        self.emit(
                            "native_error",
                            {
                                "command": "audio",
                                "message": (
                                    "I couldn't play that response. The text is still available."
                                ),
                            },
                        )
                    continue
                if kind.startswith("tts_") and data.get("client_id") not in {None, CLIENT_ID}:
                    continue
                self.emit(kind, data)

    def stream_forever(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            started = time.monotonic()
            reason = "offline"
            try:
                self.stream_once()
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    reason = "unauthorized"
                    self.emit(
                        "native_error",
                        {
                            "command": "connection",
                            "message": (
                                "Thursday couldn't authorize this desktop connection. "
                                "Check THURSDAY_API_TOKEN in the local .env, then restart Thursday."
                            ),
                        },
                    )
            except (OSError, ValueError, TimeoutError, http.client.HTTPException):
                pass
            if self._stop.is_set():
                break
            self.emit("native_connection", {"connected": False, "reason": reason})
            if time.monotonic() - started > 5:
                delay = 0.5
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 8)

    def _health_loop(self) -> None:
        while not self._stop.is_set():
            self.health()
            self._expire_audio()
            self._stop.wait(5)

    def start(self) -> None:
        self.emit("native_settings", self.settings)
        threading.Thread(target=self.stream_forever, daemon=True).start()
        threading.Thread(target=self._health_loop, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        with self._audio_lock:
            self._output_observer.stop()
            for path in list(self._audio_files.values()):
                path.unlink(missing_ok=True)
            self._audio_files.clear()

    def command(self, command: dict[str, Any]) -> None:
        """Only a small allowlist can cross from QML into the local privileged API."""
        kind = command.get("command")
        try:
            if kind == "settings":
                values = command.get("values", {})
                self.settings.update(
                    {k: v for k, v in values.items() if k in self.settings and isinstance(v, bool)}
                )
                private_write(self.settings_path, json.dumps(self.settings).encode())
                self.emit("native_settings", self.settings)
                return
            if kind == "audio_release":
                with self._audio_lock:
                    path = self._audio_files.pop(str(command.get("audio_id")), None)
                    if path:
                        path.unlink(missing_ok=True)
                    if not self._audio_files:
                        self._output_observer.stop()
                return
            if kind == "open_web":
                try:
                    result = open_web_app(self.base_url, start_service=True)
                    self.emit("native_command_result", {"command": kind, "result": result})
                except WebLaunchError as exc:
                    self.emit("native_error", {"command": kind, "message": str(exc)})
                return
            if kind == "health":
                self.health()
                return
            if kind == "conversation":
                conversation_id = command.get("conversation_id")
                if type(conversation_id) is not int or conversation_id <= 0:
                    _invalid("Invalid conversation reference")
                snapshot = self.json_request(f"/api/conversations/{conversation_id}")
                if snapshot.get("id") != conversation_id:
                    _invalid("Mismatched conversation snapshot")
                self.emit(
                    "native_conversation",
                    {
                        "conversation_id": conversation_id,
                        "request_id": command.get("request_id"),
                        "messages": snapshot.get("messages", []),
                    },
                )
                return
            if kind == "message":
                prompt = str(command.get("prompt", "")).strip()
                if not prompt:
                    return
                payload = {
                    "prompt": prompt,
                    "tts": self.settings["speak_typed"] and not self.settings["muted"],
                    "conversation_id": command.get("conversation_id"),
                    "client_id": CLIENT_ID,
                }
                path = "/api/message"
            elif kind == "confirm":
                path = "/api/confirm"
                payload = {
                    "id": str(command.get("id", "")),
                    "approved": command.get("approved") is True,
                }
            elif kind in {"voice_start", "voice_stop", "voice_cancel"}:
                path = "/api/voice/" + kind.removeprefix("voice_")
                payload = {} if kind == "voice_start" else {"session_id": command.get("session_id")}
            elif kind in {"task_cancel", "task_resume"}:
                task_id = str(command.get("task_id", ""))
                if not re.fullmatch(r"[\w-]+", task_id):
                    _invalid("Invalid task reference")
                path = "/api/tasks/" + task_id + "/" + kind.removeprefix("task_")
                payload = {}
            elif kind == "playback":
                state = command.get("state")
                if state not in {"playing", "idle", "failed"}:
                    _invalid("Invalid playback state")
                path = "/api/playback"
                payload = {
                    "client_id": CLIENT_ID,
                    "generation_id": command.get("generation_id"),
                    "state": state,
                }
            else:
                _invalid("Unknown native command")
            data = self.json_request(path, payload)
            self.emit("native_command_result", {"command": kind, "result": data})
        except urllib.error.HTTPError as exc:
            if kind == "playback" and exc.code == 409:
                # An interrupt or newer utterance legitimately retires old feedback.
                return
            messages = {
                401: "Thursday's local connection needs its configured API token.",
                403: "Thursday couldn't authorize that local action.",
                404: "That action has expired. Open the current conversation and try again.",
                409: "A task is already running. Stop it or wait before trying again.",
                503: "Thursday is still starting. Please try again in a moment.",
            }
            self.emit(
                "native_error",
                {
                    "command": kind,
                    "message": messages.get(
                        exc.code, "Thursday couldn't complete that action. Please try again."
                    ),
                },
            )
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            self.emit(
                "native_error",
                {
                    "command": kind,
                    "message": "I couldn't reach Thursday. Check the connection and try again.",
                },
            )


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    host = os.getenv("THURSDAY_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    base = os.getenv("THURSDAY_BASE_URL", f"http://{host}:{os.getenv('THURSDAY_PORT', '5005')}")

    def output(event: dict[str, Any]) -> None:
        try:
            print(json.dumps(event, ensure_ascii=False), flush=True)
        except BrokenPipeError:
            os._exit(0)

    client = NativeClient(base, os.getenv("THURSDAY_API_TOKEN"), output)
    client.start()
    try:
        for line in sys.stdin:
            if len(line) > 128 * 1024:
                continue
            with contextlib.suppress(ValueError, TypeError):
                command = json.loads(line)
                if isinstance(command, dict):
                    client.command(command)
    finally:
        client.close()


if __name__ == "__main__":
    main()
