"""SQLite session persistence — save/load conversation state across restarts."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)


class SessionStore:
    """SQLite-backed storage for agent sessions."""

    def __init__(self, db_path: str = "shipyard.db"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at REAL,
                working_directory TEXT,
                injected_context TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS session_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_data TEXT NOT NULL,
                created_at REAL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS file_read_tracker (
                session_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                read_time REAL,
                PRIMARY KEY (session_id, file_path),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)
        self._conn.commit()

    def create_session(self, session_id: str, working_directory: str = ".") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (id, created_at, working_directory) VALUES (?, ?, ?)",
            (session_id, time.time(), working_directory),
        )
        self._conn.commit()

    def save_messages(self, session_id: str, messages: list) -> None:
        """Save all messages for a session (replaces existing)."""
        self._conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
        data = messages_to_dict(messages)
        for item in data:
            self._conn.execute(
                "INSERT INTO session_messages (session_id, message_data, created_at) VALUES (?, ?, ?)",
                (session_id, json.dumps(item), time.time()),
            )
        self._conn.commit()

    def load_messages(self, session_id: str) -> list:
        """Load messages for a session."""
        rows = self._conn.execute(
            "SELECT message_data FROM session_messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        if not rows:
            return []
        data = [json.loads(row["message_data"]) for row in rows]
        return messages_from_dict(data)

    def save_context(self, session_id: str, context: list[dict]) -> None:
        self._conn.execute(
            "UPDATE sessions SET injected_context = ? WHERE id = ?",
            (json.dumps(context), session_id),
        )
        self._conn.commit()

    def load_context(self, session_id: str) -> list[dict]:
        row = self._conn.execute(
            "SELECT injected_context FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row and row["injected_context"]:
            return json.loads(row["injected_context"])
        return []

    def save_file_tracker(self, session_id: str, tracker_data: dict[str, float]) -> None:
        self._conn.execute("DELETE FROM file_read_tracker WHERE session_id = ?", (session_id,))
        for path, read_time in tracker_data.items():
            self._conn.execute(
                "INSERT INTO file_read_tracker (session_id, file_path, read_time) VALUES (?, ?, ?)",
                (session_id, path, read_time),
            )
        self._conn.commit()

    def load_file_tracker(self, session_id: str) -> dict[str, float]:
        rows = self._conn.execute(
            "SELECT file_path, read_time FROM file_read_tracker WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {row["file_path"]: row["read_time"] for row in rows}

    def delete_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM file_read_tracker WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, created_at, working_directory FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
