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
    expires_at: float = 0.0


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
            import time
            if not item or item.event.is_set() or time.time() >= item.expires_at:
                return False
            item.approved = approved
            item.event.set()
            return True

    def snapshot(self) -> list[dict]:
        """Recover actionable confirmations when a native client reconnects."""
        import time
        with self._lock:
            return [{"id": item.id, "prompt": item.prompt,
                     "timeout_sec": max(0, item.expires_at - time.time())}
                    for item in self._pending.values()
                    if not item.event.is_set() and item.expires_at > time.time()]

    def request(self, prompt: str, timeout_sec: float | None = None, cancel_event=None, force_web=False) -> bool:
        timeout = timeout_sec if timeout_sec is not None else self.default_timeout_sec

        # Interactive CLI
        if sys.stdin.isatty() and not force_web:
            try:
                reply = input(f"{prompt} [y/N]: ").strip().lower()
                return reply in {"y", "yes"}
            except (EOFError, KeyboardInterrupt):
                return False

        # Web / non-TTY: wait for API confirmation
        confirm_id = uuid.uuid4().hex[:12]
        import time
        item = PendingConfirm(id=confirm_id, prompt=prompt, expires_at=time.time() + timeout)
        with self._lock:
            self._pending[confirm_id] = item

        if self._broadcast:
            self._broadcast(
                "confirm_required",
                {"id": confirm_id, "prompt": prompt, "timeout_sec": timeout},
            )

        import time
        deadline = time.monotonic() + timeout
        while not item.event.is_set() and time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                break
            item.event.wait(min(0.1, max(0, deadline - time.monotonic())))
        approved = item.event.is_set() and item.approved and not (cancel_event and cancel_event.is_set())
        with self._lock:
            self._pending.pop(confirm_id, None)

        if self._broadcast:
            self._broadcast(
                "confirm_resolved",
                {"id": confirm_id, "approved": approved},
            )
        return approved


confirm_broker = ConfirmBroker()
