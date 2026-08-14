"""Thread-safe command bridge for Thursday's main-profile Brave extension."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class BrowserBridgeError(RuntimeError):
    """A user-facing failure from the main-profile browser bridge."""


_UNAVAILABLE_MESSAGE = (
    "Thursday opened Gmail, but its main Brave profile helper did not connect. "
    "Use the installed Brave launcher once so the Thursday helper is loaded, then retry."
)


@dataclass
class _PendingCommand:
    command_id: str
    action: str
    payload: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None

    def public_payload(self) -> dict[str, Any]:
        return {"id": self.command_id, "action": self.action, "payload": self.payload}


class BrowserBridge:
    """Exchange allow-listed browser commands over Thursday's loopback server."""

    def __init__(self) -> None:
        self._commands: queue.Queue[_PendingCommand] = queue.Queue()
        self._pending: dict[str, _PendingCommand] = {}
        self._lock = threading.Lock()
        self._last_poll_at: float | None = None

    def request(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 90,
    ) -> dict[str, Any]:
        command = _PendingCommand(
            command_id=uuid.uuid4().hex,
            action=action,
            payload=payload or {},
        )
        with self._lock:
            self._pending[command.command_id] = command
        self._commands.put(command)
        if not command.event.wait(timeout):
            with self._lock:
                self._pending.pop(command.command_id, None)
            raise BrowserBridgeError(_UNAVAILABLE_MESSAGE)
        result = command.result or {}
        if not result.get("success", False):
            raise BrowserBridgeError(
                str(result.get("error") or "The Brave helper could not complete the request.")
            )
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    def next_command(self, *, timeout: float = 20) -> dict[str, Any] | None:
        with self._lock:
            self._last_poll_at = time.monotonic()
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
            pending = len(self._pending)
        connected = last_poll_at is not None and time.monotonic() - last_poll_at <= 30
        return {"connected": connected, "pending": pending}

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
        with self._lock:
            command = self._pending.pop(command_id, None)
        if command is None:
            return False
        command.result = {
            "success": success,
            "data": data or {},
            "error": error,
        }
        command.event.set()
        return True


browser_bridge = BrowserBridge()
