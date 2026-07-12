from __future__ import annotations

"""Web-aware confirmation for dangerous tool actions.

CLI: prompts on stdin.
Web: broadcasts a confirm_required SSE event and waits for POST /api/confirm.
"""

import sys
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PendingConfirm:
    id: str
    prompt: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class ConfirmBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingConfirm] = {}
        self._broadcast: Callable[[str, dict], None] | None = None
        self.default_timeout_sec: float = 60.0

    def set_broadcast(self, broadcast: Callable[[str, dict], None]) -> None:
        self._broadcast = broadcast

    def resolve(self, confirm_id: str, approved: bool) -> bool:
        with self._lock:
            item = self._pending.get(confirm_id)
            if not item:
                return False
            item.approved = approved
            item.event.set()
            return True

    def request(self, prompt: str, timeout_sec: float | None = None) -> bool:
        timeout = timeout_sec if timeout_sec is not None else self.default_timeout_sec

        # Interactive CLI
        if sys.stdin.isatty():
            try:
                reply = input(f"{prompt} [y/N]: ").strip().lower()
                return reply in {"y", "yes"}
            except (EOFError, KeyboardInterrupt):
                return False

        # Web / non-TTY: wait for API confirmation
        confirm_id = uuid.uuid4().hex[:12]
        item = PendingConfirm(id=confirm_id, prompt=prompt)
        with self._lock:
            self._pending[confirm_id] = item

        if self._broadcast:
            self._broadcast(
                "confirm_required",
                {"id": confirm_id, "prompt": prompt, "timeout_sec": timeout},
            )

        approved = item.event.wait(timeout=timeout) and item.approved
        with self._lock:
            self._pending.pop(confirm_id, None)

        if self._broadcast:
            self._broadcast(
                "confirm_resolved",
                {"id": confirm_id, "approved": approved},
            )
        return approved


confirm_broker = ConfirmBroker()
