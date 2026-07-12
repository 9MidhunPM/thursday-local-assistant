from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Iterable


@dataclass(frozen=True)
class Preference:
    key: str
    value: str


@dataclass(frozen=True)
class NamedMemory:
    name: str
    content: str
    importance: float = 0.5
    last_accessed_at: str | None = None


@dataclass(frozen=True)
class KnowledgeFact:
    subject: str
    predicate: str
    object: str
    importance: float = 0.5
    last_accessed_at: str | None = None


def _normalize_text(value: str) -> str:
    return value.strip().lower()


def _tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}


def _score_match(query: str, *fields: str) -> int:
    """Token-overlap fallback scorer used when FTS5 is unavailable."""
    normalized_query = _normalize_text(query)
    query_tokens = _tokenize(query)
    score = 0
    for field in fields:
        normalized_field = _normalize_text(field)
        if not normalized_field:
            continue
        if normalized_query and normalized_query in normalized_field:
            score += 6
        field_tokens = _tokenize(field)
        score += len(query_tokens & field_tokens) * 2
    return score


def _recency_boost(last_accessed_at: str | None) -> float:
    """Decay from 1.0 (just accessed) toward 0 over ~30 days."""
    if not last_accessed_at:
        return 0.2
    try:
        ts = datetime.fromisoformat(last_accessed_at)
    except ValueError:
        return 0.2
    age_days = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)
    # half-life of ~14 days
    return max(0.0, 0.5 ** (age_days / 14.0))


class LongTermMemory:
    """Personal long-term memory store.

    Backed by SQLite. Uses FTS5 full-text search when available (with BM25
    ranking blended with importance and recency), and gracefully falls back to
    token-overlap scoring on SQLite builds without FTS5.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._has_fts = False
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS named_memories (
                    name TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_usage (
                    command TEXT PRIMARY KEY,
                    count INTEGER NOT NULL,
                    last_used_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_facts (
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    PRIMARY KEY (subject, predicate, object)
                )
                """
            )
            # Safe column migrations for pre-existing databases.
            self._ensure_column(conn, "named_memories", "importance", "REAL NOT NULL DEFAULT 0.5")
            self._ensure_column(conn, "named_memories", "last_accessed_at", "TEXT")
            self._ensure_column(conn, "knowledge_facts", "importance", "REAL NOT NULL DEFAULT 0.5")
            self._ensure_column(conn, "knowledge_facts", "last_accessed_at", "TEXT")

            # FTS5 indexes (optional). Fallback to token overlap if unsupported.
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                    "name, content, content='')"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
                    "subject, predicate, object, content='')"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS preferences_fts USING fts5("
                    "key, value, content='')"
                )
                self._has_fts = True
            except sqlite3.OperationalError:
                self._has_fts = False

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ---------------- preferences ----------------

    def set_preference(self, key: str, value: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO preferences (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, timestamp),
            )
            if self._has_fts:
                conn.execute("DELETE FROM preferences_fts WHERE key = ?", (key,))
                conn.execute(
                    "INSERT INTO preferences_fts (key, value) VALUES (?, ?)", (key, value)
                )

    def get_preference(self, key: str) -> Preference | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT key, value FROM preferences WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        return Preference(key=row["key"], value=row["value"])

    def delete_preference(self, key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
            return cursor.rowcount > 0

    def list_preferences(self) -> Iterable[Preference]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM preferences").fetchall()
        return [Preference(key=row["key"], value=row["value"]) for row in rows]

    def search_preferences(self, query: str, limit: int = 5) -> list[Preference]:
        if self._has_fts:
            fts = self._fts_preferences(query, limit)
            if fts:
                return fts
        ranked = sorted(
            ((_score_match(query, pref.key, pref.value), pref) for pref in self.list_preferences()),
            key=lambda item: item[0],
            reverse=True,
        )
        return [pref for score, pref in ranked if score > 0][:limit]

    def _fts_preferences(self, query: str, limit: int) -> list[Preference] | None:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT p.key, p.value FROM preferences_fts f "
                    "JOIN preferences p ON p.key = f.key "
                    "WHERE preferences_fts MATCH ? "
                    "ORDER BY bm25(preferences_fts) LIMIT ?",
                    (self._fts_query(query), limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return None
        return [Preference(key=row["key"], value=row["value"]) for row in rows]

    # ---------------- named memories ----------------

    def store_memory(
        self, name: str, content: str, timestamp: str, importance: float = 0.5
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO named_memories (name, content, importance, created_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    content = excluded.content,
                    importance = MAX(named_memories.importance, excluded.importance),
                    last_accessed_at = excluded.last_accessed_at
                """,
                (name, content, importance, timestamp, timestamp),
            )
            if self._has_fts:
                conn.execute("DELETE FROM memories_fts WHERE name = ?", (name,))
                conn.execute(
                    "INSERT INTO memories_fts (name, content) VALUES (?, ?)", (name, content)
                )

    def recall_memory(self, name: str) -> NamedMemory | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, content, importance, last_accessed_at FROM named_memories WHERE name = ?",
                (name,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE named_memories SET last_accessed_at = ? WHERE name = ?",
                    (datetime.now(timezone.utc).isoformat(), name),
                )
        if not row:
            return None
        return NamedMemory(
            name=row["name"],
            content=row["content"],
            importance=row["importance"],
            last_accessed_at=row["last_accessed_at"],
        )

    def delete_memory(self, name: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM named_memories WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def list_memories(self) -> list[NamedMemory]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, content, importance, last_accessed_at "
                "FROM named_memories ORDER BY created_at DESC"
            ).fetchall()
        return [
            NamedMemory(
                name=row["name"],
                content=row["content"],
                importance=row["importance"],
                last_accessed_at=row["last_accessed_at"],
            )
            for row in rows
        ]

    def search_memories(self, query: str, limit: int = 5) -> list[NamedMemory]:
        if self._has_fts:
            fts = self._fts_memories(query, limit)
            if fts:
                return fts
        ranked = sorted(
            (
                (
                    _score_match(query, memory.name, memory.content)
                    + int(memory.importance * 4)
                    + int(_recency_boost(memory.last_accessed_at) * 3),
                    memory,
                )
                for memory in self.list_memories()
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        return [memory for score, memory in ranked if score > 0][:limit]

    def _fts_memories(self, query: str, limit: int) -> list[NamedMemory] | None:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT m.name, m.content, m.importance, m.last_accessed_at "
                    "FROM memories_fts f JOIN named_memories m ON m.name = f.name "
                    "WHERE memories_fts MATCH ? "
                    "ORDER BY bm25(memories_fts) LIMIT ?",
                    (self._fts_query(query), limit * 3),
                ).fetchall()
        except sqlite3.OperationalError:
            return None
        ranked = self._rerank(rows, query, lambda r: (r["name"], r["content"]))
        return ranked[:limit]

    # ---------------- knowledge facts ----------------

    def remember_fact(
        self,
        subject: str,
        predicate: str,
        object_value: str,
        timestamp: str,
        importance: float = 0.5,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_facts
                    (subject, predicate, object, importance, updated_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject, predicate, object) DO UPDATE SET
                    importance = MAX(knowledge_facts.importance, excluded.importance),
                    updated_at = excluded.updated_at,
                    last_accessed_at = excluded.last_accessed_at
                """,
                (subject, predicate, object_value, importance, timestamp, timestamp),
            )
            if self._has_fts:
                conn.execute(
                    "DELETE FROM facts_fts WHERE subject = ? AND predicate = ? AND object = ?",
                    (subject, predicate, object_value),
                )
                conn.execute(
                    "INSERT INTO facts_fts (subject, predicate, object) VALUES (?, ?, ?)",
                    (subject, predicate, object_value),
                )

    def get_entity_facts(self, subject: str) -> list[KnowledgeFact]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT subject, predicate, object, importance, last_accessed_at
                FROM knowledge_facts
                WHERE lower(subject) = lower(?)
                ORDER BY importance DESC, updated_at DESC
                """,
                (subject,),
            ).fetchall()
            if rows:
                conn.execute(
                    "UPDATE knowledge_facts SET last_accessed_at = ? WHERE lower(subject) = lower(?)",
                    (datetime.now(timezone.utc).isoformat(), subject),
                )
        return [
            KnowledgeFact(
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                importance=row["importance"],
                last_accessed_at=row["last_accessed_at"],
            )
            for row in rows
        ]

    def delete_fact(self, subject: str, predicate: str, object_value: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge_facts WHERE lower(subject) = lower(?) "
                "AND lower(predicate) = lower(?) AND lower(object) = lower(?)",
                (subject, predicate, object_value),
            )
            return cursor.rowcount > 0

    def delete_entity_facts(self, subject: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge_facts WHERE lower(subject) = lower(?)", (subject,)
            )
            return cursor.rowcount

    def forget_everything(self) -> dict[str, int]:
        with self._connect() as conn:
            prefs = conn.execute("DELETE FROM preferences").rowcount
            memories = conn.execute("DELETE FROM named_memories").rowcount
            facts = conn.execute("DELETE FROM knowledge_facts").rowcount
            if self._has_fts:
                for table in ("preferences_fts", "memories_fts", "facts_fts"):
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS preferences_fts USING fts5("
                    "key, value, content='')"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                    "name, content, content='')"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
                    "subject, predicate, object, content='')"
                )
        return {"preferences_deleted": prefs, "memories_deleted": memories, "facts_deleted": facts}

    def search_facts(self, query: str, limit: int = 8) -> list[KnowledgeFact]:
        if self._has_fts:
            fts = self._fts_facts(query, limit)
            if fts:
                return fts
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT subject, predicate, object, importance, last_accessed_at FROM knowledge_facts"
            ).fetchall()
        ranked = sorted(
            (
                (
                    _score_match(query, row["subject"], row["predicate"], row["object"])
                    + int(row["importance"] * 4)
                    + int(_recency_boost(row["last_accessed_at"]) * 3),
                    row,
                )
                for row in rows
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        return [
            KnowledgeFact(
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                importance=row["importance"],
                last_accessed_at=row["last_accessed_at"],
            )
            for score, row in ranked
            if score > 0
        ][:limit]

    def _fts_facts(self, query: str, limit: int) -> list[KnowledgeFact] | None:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT k.subject, k.predicate, k.object, k.importance, k.last_accessed_at "
                    "FROM facts_fts f JOIN knowledge_facts k "
                    "ON k.subject = f.subject AND k.predicate = f.predicate AND k.object = f.object "
                    "WHERE facts_fts MATCH ? "
                    "ORDER BY bm25(facts_fts) LIMIT ?",
                    (self._fts_query(query), limit * 3),
                ).fetchall()
        except sqlite3.OperationalError:
            return None
        ranked = self._rerank(
            rows, query, lambda r: (r["subject"], r["predicate"], r["object"])
        )
        return [
            KnowledgeFact(
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                importance=row["importance"],
                last_accessed_at=row["last_accessed_at"],
            )
            for row in ranked[:limit]
        ]

    def list_facts(self) -> list[KnowledgeFact]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT subject, predicate, object, importance, last_accessed_at "
                "FROM knowledge_facts ORDER BY updated_at DESC"
            ).fetchall()
        return [
            KnowledgeFact(
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                importance=row["importance"],
                last_accessed_at=row["last_accessed_at"],
            )
            for row in rows
        ]

    # ---------------- shared helpers ----------------

    @staticmethod
    def _fts_query(query: str) -> str:
        # Quote tokens so punctuation in the query doesn't break FTS5 syntax.
        tokens = [t for t in re.findall(r"[A-Za-z0-9]+", query) if len(t) > 1]
        if not tokens:
            return '""'
        return " ".join(f'"{t}"' for t in tokens)

    def _rerank(self, rows: Iterable[sqlite3.Row], query: str, fields_fn) -> list[sqlite3.Row]:
        """Blend BM25 (pre-sorted) order with importance + recency + token overlap."""
        scored: list[tuple[float, sqlite3.Row]] = []
        for rank, row in enumerate(rows):
            fields = fields_fn(row)
            overlap = _score_match(query, *fields)
            importance = float(row["importance"]) if "importance" in row.keys() else 0.5
            recency = _recency_boost(row["last_accessed_at"] if "last_accessed_at" in row.keys() else None)
            # Lower BM25 rank index = better; convert to a positive score.
            bm25_score = max(0.0, 20.0 - rank * 2.0)
            total = bm25_score + overlap + importance * 5.0 + recency * 4.0
            scored.append((total, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored]

    def build_context(self, query: str, limit: int = 4) -> dict[str, list[dict[str, str]]]:
        preferences = [
            {"key": pref.key, "value": pref.value}
            for pref in self.search_preferences(query, limit=limit)
        ]
        memories = [
            {"name": memory.name, "content": memory.content}
            for memory in self.search_memories(query, limit=limit)
        ]
        facts = [
            {"subject": fact.subject, "predicate": fact.predicate, "object": fact.object}
            for fact in self.search_facts(query, limit=limit * 2)
        ]
        return {
            "preferences": preferences,
            "memories": memories,
            "facts": facts,
        }

    def record_command(self, command: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO command_usage (command, count, last_used_at)
                VALUES (?, 1, ?)
                ON CONFLICT(command) DO UPDATE SET
                    count = command_usage.count + 1,
                    last_used_at = excluded.last_used_at
                """,
                (command, timestamp),
            )
