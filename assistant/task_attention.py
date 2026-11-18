"""Quiet desktop notices for completed or paused, user-requested tasks."""

import contextlib
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from assistant.tools.sumi_tools import visible_sumi_route


class TaskAttention:
    def __init__(self) -> None:
        self._announced: deque[str] = deque(maxlen=64)
        self._lock = threading.Lock()

    def finish(self, task: dict[str, Any]) -> str | None:
        if task.get("desktop_task") is not True or not task.get("id"):
            return None
        status = task.get("status")
        if status == "completed":
            message = "Desktop task needs your input." if task.get("needs_decision") else (
                "Desktop task finished."
            )
        elif status in {"paused", "failed"}:
            message = f"Desktop task {status}. Open Thursday to review."
        else:
            return None
        with self._lock:
            if task["id"] in self._announced:
                return None
            self._announced.append(task["id"])
        return message


def notify_if_offscreen(
    conversation_id: int,
    message: str,
    has_view: Callable[[int], bool],
    active_conversation: Callable[[], int | None],
) -> None:
    if has_view(conversation_id):
        return
    # Sumi's own adapter follows the active server conversation, and its
    # existing read-only status endpoint can tell us whether the chat is open.
    if visible_sumi_route() == "thursday" and active_conversation() == conversation_id:
        return
    if not has_view(conversation_id):
        _send(message)


def _send(message: str) -> None:
    # The durable task remains available even on desktops without a daemon.
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["notify-send", "--app-name=Thursday", "--urgency=low", "--expire-time=5000",
             message],
            capture_output=True,
            timeout=3,
            check=False,
        )
