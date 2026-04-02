"""Session and chat history persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

from app.storage.database import Database


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionRepository:
    """Stores sessions and chat messages in SQLite."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(self, session_id: str | None = None) -> str:
        assigned_id = session_id or str(uuid.uuid4())
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO sessions (id, created_at) VALUES (?, ?)",
                (assigned_id, _utcnow()),
            )
        return assigned_id

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        route: str = "",
        latency_ms: float = 0.0,
        metadata: dict[str, object] | None = None,
    ) -> int:
        self.create_session(session_id)
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO chat_messages (
                    session_id, role, content, route, latency_ms, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    route,
                    latency_ms,
                    json.dumps(metadata or {}, ensure_ascii=True),
                    _utcnow(),
                ),
            )
            return int(cursor.lastrowid)

    def list_sessions(self, limit: int = 50) -> list[dict[str, object]]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT s.id, s.created_at, COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN chat_messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_messages(self, session_id: str, limit: int = 200) -> list[dict[str, object]]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT id, session_id, role, content, route, latency_ms, metadata_json, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = cursor.fetchall()
        messages = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            except Exception:
                item["metadata"] = {}
            messages.append(item)
        return messages

