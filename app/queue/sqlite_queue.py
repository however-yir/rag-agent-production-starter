"""SQLite queue backend (polling-based, no external broker)."""

from __future__ import annotations

from datetime import datetime

from app.queue.base import QueueBackend, QueueJobRef


class SqliteQueueBackend:
    name = "sqlite"

    def enqueue(self, *, job_id: str, available_at: datetime | None = None) -> None:
        # SQLite backend relies on DB polling; no explicit push is required.
        _ = job_id
        _ = available_at

    def dequeue(self) -> QueueJobRef | None:
        return None

    def ack(self, *, job_id: str) -> None:
        _ = job_id

    def nack(self, *, job_id: str, retry_at: datetime | None = None, dead_letter: bool = False) -> None:
        _ = job_id
        _ = retry_at
        _ = dead_letter
