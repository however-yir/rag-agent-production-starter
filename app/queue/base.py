"""Queue interface abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(slots=True)
class QueueJobRef:
    job_id: str
    dequeued_at: datetime


class QueueBackend(Protocol):
    name: str

    def enqueue(self, *, job_id: str, available_at: datetime | None = None) -> None:
        """Queue a job id for processing."""

    def dequeue(self) -> QueueJobRef | None:
        """Return the next available queued job id, if any."""

    def ack(self, *, job_id: str) -> None:
        """Mark queue delivery done."""

    def nack(self, *, job_id: str, retry_at: datetime | None = None, dead_letter: bool = False) -> None:
        """Requeue or dead-letter an unsuccessfully processed job."""
