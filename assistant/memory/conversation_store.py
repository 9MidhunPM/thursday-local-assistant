from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConversationSummary:
    id: int
    title: str
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """Persistent storage for multiple chat conversations and their messages.

    Shares the same SQLite database file as the long-term memory store but uses
    its own tables, so the two stores never collide.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT 'New Chat',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_name TEXT,
                    tool_call_id TEXT,
                    tool_calls_json TEXT,
                    seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id)
                        REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_msg_conv "
                "ON conversation_messages(conversation_id, seq)"
            )

    # ---- conversations ----

    def create_conversation(self, title: str = "New Chat") -> int:
        ts = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, ts, ts),
            )
            return int(cur.lastrowid)

    def list_conversations(self, limit: int = 100) -> list[ConversationSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at "
                "FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            ConversationSummary(
                id=r["id"], title=r["title"], created_at=r["created_at"], updated_at=r["updated_at"]
            )
            for r in rows
        ]

    def get_conversation(self, conversation_id: int) -> ConversationSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return ConversationSummary(
            id=row["id"], title=row["title"], created_at=row["created_at"], updated_at=row["updated_at"]
        )

    def rename_conversation(self, conversation_id: int, title: str) -> bool:
        ts = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, ts, conversation_id),
            )
            return cur.rowcount > 0

    def delete_conversation(self, conversation_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            return cur.rowcount > 0

    def touch(self, conversation_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now_iso(), conversation_id),
            )

    # ---- messages ----

    def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str | None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> int:
        ts = _now_iso()
        tool_calls_json = json.dumps(tool_calls, ensure_ascii=True) if tool_calls else None
        with self._connect() as conn:
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq "
                "FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            seq = int(seq_row["next_seq"])
            cur = conn.execute(
                """
                INSERT INTO conversation_messages
                    (conversation_id, role, content, tool_name, tool_call_id, tool_calls_json, seq, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, tool_name, tool_call_id, tool_calls_json, seq, ts),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (ts, conversation_id),
            )
            return int(cur.lastrowid)

    def get_messages(self, conversation_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, tool_name, tool_call_id, tool_calls_json "
                "FROM conversation_messages WHERE conversation_id = ? ORDER BY seq ASC",
                (conversation_id,),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for r in rows:
            item: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["tool_name"]:
                item["tool_name"] = r["tool_name"]
            if r["tool_call_id"]:
                item["tool_call_id"] = r["tool_call_id"]
            if r["tool_calls_json"]:
                try:
                    item["tool_calls"] = json.loads(r["tool_calls_json"])
                except json.JSONDecodeError:
                    pass
            messages.append(item)
        return messages

    def message_count(self, conversation_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return int(row["n"])
