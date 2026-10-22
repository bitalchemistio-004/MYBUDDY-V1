"""SQLite-backed persistence for MBUDDY memory."""
from __future__ import annotations

import json
import os
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import MEMORY_FILE

DB_PATH = Path(os.getenv("MBUDDY_SQLITE_PATH", "memory/mbuddy.sqlite3"))


def default_memory() -> dict[str, Any]:
    return {
        "core_facts": [],
        "emotional_memories": [],
        "conversation_log": [],
        "inner_log": [],
        "shared_timeline": [],
        "project_threads": [],
        "reminders": [],
        "daily_summaries": [],
        "facts": [],
        "mood_history": [],
        "identity_snapshot": "",
        "approved_languages": ["ar", "en"],
        "active_language": "en",
        "relationship_state": {
            "rapport": 50,
            "trust": 50,
            "current_vibe": "steady",
            "last_meaningful_topic": "",
            "preferred_help_style": "direct",
            "history_of_mood_patterns": {
                "tends_to_be_stressed_around": [],
                "responds_well_to": "direct answers first, then light jokes",
                "gets_annoyed_by": "over-explanation",
            },
        },
    }


class SQLiteMemoryStore:
    """Stores a JSON snapshot plus queryable companion tables."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_snapshot (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT UNIQUE,
                    time TEXT,
                    user TEXT,
                    mbuddy TEXT,
                    lang TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS core_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact TEXT UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS emotional_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT UNIQUE,
                    time TEXT,
                    emotion TEXT,
                    memory TEXT
                )
                """
            )
            self._ensure_column(conn, "conversation_log", "uid", "TEXT")
            self._ensure_column(conn, "emotional_memories", "uid", "TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_log_uid "
                "ON conversation_log(uid)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_emotional_memories_uid "
                "ON emotional_memories(uid)"
            )

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def load(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM memory_snapshot WHERE id = 1").fetchone()
        if row:
            try:
                data = json.loads(row[0])
                base = default_memory()
                base.update(data)
                return base
            except json.JSONDecodeError:
                return default_memory()

        migrated = self._load_json_file()
        self.save(migrated)
        return migrated

    def save(self, memory: dict[str, Any]) -> None:
        data = default_memory()
        data.update(memory)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = json.dumps(data, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_snapshot (id, data, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                """,
                (payload, now),
            )
            self._sync_tables(conn, data)

    def _load_json_file(self) -> dict[str, Any]:
        if not os.path.exists(MEMORY_FILE):
            return default_memory()
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            base = default_memory()
            base.update(data)
            return base
        except (json.JSONDecodeError, OSError):
            return default_memory()

    def _sync_tables(self, conn: sqlite3.Connection, memory: dict[str, Any]) -> None:
        conn.executemany(
            """
            INSERT OR IGNORE INTO conversation_log (uid, time, user, mbuddy, lang)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    self._entry_uid(
                        entry.get("time", ""),
                        entry.get("user", ""),
                        entry.get("mbuddy", ""),
                        entry.get("lang", ""),
                    ),
                    entry.get("time", ""),
                    entry.get("user", ""),
                    entry.get("mbuddy", ""),
                    entry.get("lang", ""),
                )
                for entry in memory.get("conversation_log", [])
            ],
        )

        # The table mirrors the snapshot (F14): a fact forgotten or replaced there is gone here too, so the Memory
        # page, the table and the prompt hold the same facts. It used to only ever grow.
        facts = [str(f) for f in memory.get("core_facts", [])]
        if facts:
            conn.execute(f"DELETE FROM core_facts WHERE fact NOT IN ({','.join('?' * len(facts))})", facts)
        else:
            conn.execute("DELETE FROM core_facts")
        conn.executemany(
            "INSERT OR IGNORE INTO core_facts (fact, created_at) VALUES (?, ?)",
            [(fact, datetime.now().strftime("%Y-%m-%d %H:%M:%S")) for fact in facts],
        )

        conn.executemany(
            """
            INSERT OR IGNORE INTO emotional_memories (uid, time, emotion, memory)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    self._entry_uid(
                        entry.get("time", ""),
                        entry.get("emotion", ""),
                        entry.get("memory", ""),
                    ),
                    entry.get("time", ""),
                    entry.get("emotion", ""),
                    entry.get("memory", ""),
                )
                for entry in memory.get("emotional_memories", [])
            ],
        )

    def _entry_uid(self, *parts: str) -> str:
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_store: SQLiteMemoryStore | None = None


def get_memory_store() -> SQLiteMemoryStore:
    global _store
    if _store is None:
        _store = SQLiteMemoryStore()
    return _store
