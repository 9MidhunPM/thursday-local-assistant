"""Durable run checkpoints. Cancellation never rolls back completed side effects."""

from __future__ import annotations

# Task failures carry precise user-facing pause reasons.
# ruff: noqa: TRY003
import json
import re
import sqlite3
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from assistant.integrations.desktop_policy import action_label
from assistant.security import redact_secrets


class TaskPaused(RuntimeError):
    pass


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.active: TaskRun | None = None
        self.on_update = None
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_runs (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            for task_id, raw in conn.execute("SELECT id,data FROM task_runs"):
                data = json.loads(raw)
                if data["status"] in {"running", "awaiting_approval"}:
                    data.update(
                        status="interrupted",
                        detail="Thursday restarted; observe state before resuming.",
                    )
                    conn.execute(
                        "UPDATE task_runs SET data=? WHERE id=?", (json.dumps(data), task_id)
                    )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT data FROM task_runs WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self, conversation_id=None, *, before=None):
        with self._lock, self._connect() as conn:
            if before:
                row = conn.execute(
                    "SELECT data FROM task_runs WHERE json_extract(data,'$.conversation_id') IS ? "
                    "AND rowid < (SELECT rowid FROM task_runs WHERE id=?) "
                    "ORDER BY rowid DESC LIMIT 1",
                    (conversation_id, before),
                ).fetchone()
            elif conversation_id is None:
                row = conn.execute(
                    "SELECT data FROM task_runs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT data FROM task_runs WHERE json_extract(data,'$.conversation_id')=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (conversation_id,),
                ).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, data):
        original_usage = data.get("usage", {})
        data = json.loads(json.dumps(redact_secrets(data)))
        # Token *counts* are not credentials. Keep a narrow numeric allowlist;
        # the generic log redactor correctly removes token strings elsewhere.
        for field in ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens"):
            value = original_usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                data["usage"][field] = value
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_runs VALUES (?,?)", (data["id"], json.dumps(data))
            )
        if self.on_update:
            self.on_update(data.copy())

    def create(self, goal, conversation_id, seconds=300, parent=None):
        with self._lock:
            if self.active and not self.active.finished:
                raise RuntimeError("A task is already running.")
            self.active = TaskRun(self, goal, conversation_id, seconds, parent)
            self.active.publish()
            return self.active

    def cancel(self, task_id):
        with self._lock:
            if self.active and self.active.data["id"] == task_id and not self.active.finished:
                self.active.cancelled.set()
                self.active.update(detail="Stopping; waiting for the current operation to release.")
                return True
        return False


class TaskRun:
    def __init__(self, store, goal, conversation_id, seconds, parent=None):
        self.store = store
        self.cancelled = threading.Event()
        self.deadline = time.monotonic() + seconds
        self.finished = False
        self.data = {
            "id": uuid.uuid4().hex,
            "conversation_id": conversation_id,
            "goal": goal,
            "status": "running",
            "step": 0,
            "detail": "Understanding your request",
            "parent_id": parent,
            "outcomes": [],
            "usage": {},
            "created_at": time.time(),
        }
        self._repeats = {}
        self._activity_lock = threading.RLock()
        self.data["activities"] = []
        self.data["activity"] = None

    def publish(self):
        self.data["updated_at"] = time.time()
        self.store.save(self.data)

    def update(self, **fields):
        self.data.update(fields)
        self.publish()

    def action_start(self, tool, arguments):
        with self._activity_lock:
            target = str(arguments.get("window") or arguments.get("target") or "")
            target = target if re.fullmatch(r"0x[0-9a-fA-F]+", target) else ""
            item = {
                "id": uuid.uuid4().hex,
                "tool": tool,
                "label": action_label(tool, arguments),
                "operation": tool.removeprefix("hypruse__"),
                "action": str(arguments.get("action", ""))[:40],
                "target": target,
                "status": "running",
                "started_at": time.time(),
            }
            self.data["activities"] = (self.data["activities"] + [item])[-50:]
            self.data["activity"] = item
            self.data["detail"] = item["label"]
            self.publish()
            return item["id"]

    def action_finish(self, action_id, status, summary):
        with self._activity_lock:
            for item in self.data["activities"]:
                if item["id"] == action_id:
                    item.update(status=status, finished_at=time.time(), summary=summary)
                    item["duration_ms"] = max(
                        0, round((item["finished_at"] - item["started_at"]) * 1000)
                    )
            self.publish()

    def check(self):
        if self.cancelled.is_set():
            raise InterruptedError(
                "Task stopped. Completed actions are retained in its checkpoint."
            )
        if time.monotonic() >= self.deadline:
            raise TaskPaused("Time budget reached. Resume from the checkpoint when ready.")

    def outcome(self, result, arguments, *, track_repeat=True):
        safe = {k: v for k, v in result.items() if k not in {"_images", "preview"}}
        safe["arguments"] = redact_secrets(arguments)
        # Keep structured evidence, never binary captures or arbitrarily large private content.
        encoded = json.dumps(redact_secrets(safe), default=str)
        if len(encoded) > 6000:
            safe = {
                "tool": result.get("tool"),
                "success": result.get("success"),
                "evidence_excerpt": encoded[:5500],
                "truncated": True,
                **{
                    key: safe[key]
                    for key in ("delivery", "error_code", "completed_steps", "sequence_child")
                    if key in safe
                },
            }
        self.data["outcomes"].append(safe)
        self.data["outcomes"] = self.data["outcomes"][-40:]
        if not track_repeat:
            self.publish()
            return
        key = json.dumps([result.get("tool"), arguments, safe], sort_keys=True, default=str)
        self._repeats[key] = self._repeats.get(key, 0) + 1
        self.publish()
        if self._repeats[key] >= 3:
            raise TaskPaused("Repeated operation produced no new evidence. Paused for review.")

    def account(self, stats, model):
        usage = self.data["usage"]
        for field in ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens"):
            usage[field] = usage.get(field, 0) + getattr(stats, field, 0)
        prices = {"gpt-6-luna": (0.10, 0.01, 0.50), "gpt-5.6-luna": (0.20, 0.02, 1.20)}
        if model in prices:
            inp, cached, out = prices[model]
            usage["estimated_usd"] = (
                (usage["prompt_tokens"] - usage["cached_tokens"]) * inp
                + usage["cached_tokens"] * cached
                + usage["completion_tokens"] * out
            ) / 1_000_000
            usage["pricing_note"] = (
                "Standard short-context estimate; excludes tools and cache writes."
            )
        self.publish()

    def finish(self, status, detail):
        self.finished = True
        self.update(status=status, detail=detail)
