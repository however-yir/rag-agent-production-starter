"""Audit log persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from app.storage.database import Database


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add_event(
        self,
        *,
        actor_type: str,
        actor_id: str,
        actor_name: str,
        action: str,
        resource_type: str,
        resource_id: str,
        status: str,
        detail: dict[str, object] | None = None,
        request_id: str = "",
        trace_id: str = "",
        session_id: str = "",
        job_id: str = "",
        ip: str = "",
    ) -> int:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO audit_logs (
                    actor_type,
                    actor_id,
                    actor_name,
                    action,
                    resource_type,
                    resource_id,
                    status,
                    detail_json,
                    request_id,
                    trace_id,
                    session_id,
                    job_id,
                    ip,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_type,
                    actor_id,
                    actor_name,
                    action,
                    resource_type,
                    resource_id,
                    status,
                    json.dumps(detail or {}, ensure_ascii=True),
                    request_id,
                    trace_id,
                    session_id,
                    job_id,
                    ip,
                    _utcnow(),
                ),
            )
            return int(cursor.lastrowid)

    def list_events(self, *, limit: int = 200) -> list[dict[str, object]]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM audit_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        events: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(str(item.pop("detail_json")))
            except Exception:
                item["detail"] = {}
            events.append(item)
        return events
