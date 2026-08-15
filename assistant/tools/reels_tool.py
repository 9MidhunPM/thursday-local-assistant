from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.integrations.browser_bridge import BrowserBridge, browser_bridge
from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.browser_control import BraveController

REELS_URL = "https://www.instagram.com/reels/"


class ReelsWatcher:
    """Track the extension-owned Reels timer without synthesizing OS key presses."""

    def __init__(
        self,
        controller: BraveController | None = None,
        bridge: BrowserBridge | None = None,
        interval_seconds: float = 15,
    ) -> None:
        self.controller = controller or BraveController()
        self.bridge = bridge or browser_bridge
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._running = False
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def _connect(self) -> tuple[bool, str | None]:
        if self.bridge.status().get("connected"):
            return True, None
        opened, error = self.controller.open_url(REELS_URL, title_hint="Instagram")
        if not opened:
            return False, error or "Instagram could not be opened in Brave."
        if not self.bridge.wait_until_connected(timeout=20):
            return False, "Thursday's managed Brave helper did not connect."
        return True, None

    def start(self) -> tuple[bool, str | None, bool]:
        with self._lock:
            already_running = self._running
        connected, error = self._connect()
        if not connected:
            return False, error, already_running
        try:
            result = self.bridge.request(
                "instagram.reels.start",
                {"interval_seconds": self.interval_seconds},
                timeout=45,
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return False, self.last_error, already_running
        with self._lock:
            self._running = bool(result.get("running", True))
        self.last_error = None
        return True, None, bool(result.get("already_running", already_running))

    def stop(self, *, notify_extension: bool = True) -> bool:
        with self._lock:
            was_running = self._running
            self._running = False
        if notify_extension and self.bridge.status().get("connected"):
            try:
                result = self.bridge.request("instagram.reels.stop", {}, timeout=10)
                return bool(result.get("stopped", was_running))
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
        return was_running


_WATCHER = ReelsWatcher()


@dataclass
class WatchReelsTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="watch_reels",
        description=(
            "Open Instagram Reels in the normal Brave profile and advance every 15 seconds "
            "only while its page is visible and focused. Runs until stop_watching_reels."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    )
    watcher: ReelsWatcher = _WATCHER

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        started, error, already_running = self.watcher.start()
        if not started:
            return {"success": False, "error": error}
        return {
            "success": True,
            "already_running": already_running,
            "interval_seconds": 15,
            "output": (
                "Instagram Reels is open. Auto-scroll is running and pauses whenever "
                "Instagram is not focused."
            ),
        }


@dataclass
class StopWatchingReelsTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="stop_watching_reels",
        description="Stop the active Instagram Reels auto-scroll session.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    watcher: ReelsWatcher = _WATCHER

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        stopped = self.watcher.stop()
        return {
            "success": True,
            "stopped": stopped,
            "output": "Reels auto-scroll stopped." if stopped else "No Reels session was running.",
        }


def stop_reels_watcher() -> None:
    _WATCHER.stop(notify_extension=False)


def get_tools() -> list[BaseTool]:
    return [WatchReelsTool(), StopWatchingReelsTool()]
