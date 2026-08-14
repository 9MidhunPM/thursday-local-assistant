from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.browser_control import BraveController
from assistant.tools.ui_automation import instagram_is_active

REELS_URL = "https://www.instagram.com/reels/"
class ReelsWatcher:
    def __init__(
        self,
        controller: BraveController | None = None,
        interval_seconds: float = 15,
    ) -> None:
        self.controller = controller or BraveController()
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> tuple[bool, str | None, bool]:
        with self._lock:
            if self.running:
                return True, None, True

            opened, error = self.controller.open_url(REELS_URL, title_hint="Instagram")
            if not opened:
                return False, error or "Instagram could not be opened in Brave.", False
            self._stop.clear()
            self.last_error = None
            self._thread = threading.Thread(
                target=self._loop,
                name="thursday-reels-watcher",
                daemon=True,
            )
            self._thread.start()
            return True, None, False

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._advance_if_active()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)

    @staticmethod
    def _advance_if_active() -> bool:
        if not instagram_is_active():
            return False
        result = subprocess.run(
            ["wtype", "-k", "DOWN"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0

    def stop(self) -> bool:
        with self._lock:
            was_running = self.running
            self._stop.set()
            thread = self._thread
        if thread:
            thread.join(timeout=2)
        return was_running


_WATCHER = ReelsWatcher()


@dataclass
class WatchReelsTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="watch_reels",
        description=(
            "Open Instagram Reels in Thursday's visible Brave profile and advance every "
            "15 seconds while Instagram remains focused. Runs until stop_watching_reels."
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
                "Instagram Reels is open. Auto-scroll is running and will pause whenever "
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
    _WATCHER.stop()


def get_tools() -> list[BaseTool]:
    return [WatchReelsTool(), StopWatchingReelsTool()]
