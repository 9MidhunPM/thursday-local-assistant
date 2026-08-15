"""Authenticated command bridge for Thursday's managed Brave helper."""

from __future__ import annotations

import hmac
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BRIDGE_PROTOCOL_VERSION = 2
HELPER_VERSION = "2.0.7"
ALLOWED_ACTIONS = frozenset(
    {
        "gmail.read_inbox",
        "gmail.open_draft",
        "calendar.read_agenda",
        "calendar.create_event",
        "calendar.update_event",
        "instagram.reels.start",
        "instagram.reels.stop",
    }
)


class BrowserBridgeError(RuntimeError):
    """A user-facing failure from the managed browser helper."""


_UNAVAILABLE_MESSAGE = (
    "Thursday's Brave helper is not connected. Open Brave normally and retry; "
    "if this continues, run the Thursday Brave helper installer."
)


def helper_data_dir() -> Path:
    return Path(
        os.getenv("THURSDAY_BRAVE_HELPER_HOME", "~/.local/share/thursday/brave-helper")
    ).expanduser()


def _installed_token() -> str:
    override = os.getenv("THURSDAY_BRAVE_HELPER_TOKEN")
    if override:
        return override.strip()
    try:
        return (helper_data_dir() / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass
class _PendingCommand:
    command_id: str
    action: str
    payload: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None

    def public_payload(self) -> dict[str, Any]:
        return {
            "protocol": BRIDGE_PROTOCOL_VERSION,
            "id": self.command_id,
            "action": self.action,
            "payload": self.payload,
        }


class BrowserBridge:
    """Exchange allow-listed commands with one authenticated local extension."""

    def __init__(self, token: str | None = None) -> None:
        self._dynamic_token = token is None
        self._token = _installed_token() if token is None else token
        self._commands: queue.Queue[_PendingCommand] = queue.Queue()
        self._pending: dict[str, _PendingCommand] = {}
        self._lock = threading.Lock()
        self._last_poll_at: float | None = None
        self._helper_version: str | None = None
        self._capabilities: tuple[str, ...] = ()

    def authenticate(self, token: str | None) -> bool:
        expected = _installed_token() if self._dynamic_token else self._token
        return bool(expected and token and hmac.compare_digest(expected, token))

    def request(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 90,
    ) -> dict[str, Any]:
        if action not in ALLOWED_ACTIONS:
            raise BrowserBridgeError(f"Unsupported Brave helper action: {action}")
        encoded = json.dumps(payload or {}, ensure_ascii=False).encode()
        if len(encoded) > 256_000:
            raise BrowserBridgeError("Brave helper command payload is too large.")
        command = _PendingCommand(uuid.uuid4().hex, action, payload or {})
        with self._lock:
            self._pending[command.command_id] = command
        self._commands.put(command)
        if not command.event.wait(timeout):
            with self._lock:
                self._pending.pop(command.command_id, None)
            raise BrowserBridgeError(_UNAVAILABLE_MESSAGE)
        result = command.result or {}
        if not result.get("success", False):
            raise BrowserBridgeError(str(result.get("error") or "The Brave helper failed."))
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    def next_command(
        self,
        *,
        timeout: float = 20,
        helper_version: str | None = None,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._last_poll_at = time.monotonic()
            self._helper_version = helper_version
            self._capabilities = tuple(
                item for item in (capabilities or []) if item in ALLOWED_ACTIONS
            )
        while True:
            try:
                command = self._commands.get(timeout=timeout)
            except queue.Empty:
                return None
            with self._lock:
                if command.command_id in self._pending:
                    return command.public_payload()

    def status(self) -> dict[str, Any]:
        with self._lock:
            last_poll_at = self._last_poll_at
            connected = last_poll_at is not None and time.monotonic() - last_poll_at <= 35
            return {
                "connected": connected,
                "pending": len(self._pending),
                "protocol": BRIDGE_PROTOCOL_VERSION,
                "helper_version": self._helper_version,
                "capabilities": list(self._capabilities),
            }

    def wait_until_connected(self, *, timeout: float = 15) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.status()["connected"]:
                return True
            time.sleep(0.1)
        return bool(self.status()["connected"])

    def resolve(
        self,
        command_id: str,
        *,
        success: bool,
        data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        if len(json.dumps(data or {}, ensure_ascii=False).encode()) > 2_000_000:
            success, data, error = False, {}, "Brave helper response exceeded the size limit."
        with self._lock:
            command = self._pending.pop(command_id, None)
        if command is None:
            return False
        command.result = {"success": success, "data": data or {}, "error": error}
        command.event.set()
        return True


browser_bridge = BrowserBridge()
