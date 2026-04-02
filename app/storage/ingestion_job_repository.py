"""Persistent ingestion queue repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import uuid

from app.storage.database import Database

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
FINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_FAILED}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(moment: datetime) -> str:
    return moment.isoformat()


def _sanitize_error(error: str, max_length: int = 500) -> str:
    cleaned = " ".join(error.strip().split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 3] + "..."


@dataclass(slots=True)
class IngestionJob:
    id: str
    knowledge_base: str
    source_name: str
    source_type: str
    payload_text: str
    metadata: dict[str, str]
    status: str
    retry_count: int
    max_retries: int
    next_attempt_at: str
    last_error: str
    document_id: str | None
    chunk_count: int
    queue_backend: str
    idempotency_key: str
    trace_id: str
    attempt_count: int
    started_at: str | None
    finished_at: str | None
    dead_lettered: bool
    created_at: str
    updated_at: str

    def to_dict(self, include_payload: bool = False) -> dict[str, object]:
        output: dict[str, object] = {
            "id": self.id,
            "knowledge_base": self.knowledge_base,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "metadata": self.metadata,
            "status": self.status,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "next_attempt_at": self.next_attempt_at,
            "last_error": self.last_error,
            "document_id": self.document_id,
            "chunk_count": self.chunk_count,
            "queue_backend": self.queue_backend,
            "idempotency_key": self.idempotency_key,
            "trace_id": self.trace_id,
            "attempt_count": self.attempt_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dead_lettered": self.dead_lettered,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_payload:
            output["payload_text"] = self.payload_text
        return output


class IngestionJobRepository:
    """CRUD and claiming semantics for ingestion jobs."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def enqueue_text_job(
        self,
        *,
        knowledge_base: str,
        source_name: str,
        source_type: str,
        payload_text: str,
        metadata: dict[str, str] | None = None,
        max_retries: int = 3,
        queue_backend: str = "sqlite",
        idempotency_key: str = "",
        trace_id: str = "",
    ) -> IngestionJob:
        normalized_key = idempotency_key.strip()
        if normalized_key:
            existing = self.get_by_idempotency_key(
                knowledge_base=knowledge_base,
                idempotency_key=normalized_key,
            )
            if existing is not None:
                return existing

        now = _utcnow()
        job = IngestionJob(
            id=str(uuid.uuid4()),
            knowledge_base=knowledge_base,
            source_name=source_name,
            source_type=source_type,
            payload_text=payload_text,
            metadata=metadata or {},
            status=STATUS_QUEUED,
            retry_count=0,
            max_retries=max_retries,
            next_attempt_at=_to_iso(now),
            last_error="",
            document_id=None,
            chunk_count=0,
            queue_backend=queue_backend,
            idempotency_key=normalized_key,
            trace_id=trace_id,
            attempt_count=0,
            started_at=None,
            finished_at=None,
            dead_lettered=False,
            created_at=_to_iso(now),
            updated_at=_to_iso(now),
        )
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO ingestion_jobs (
                    id,
                    knowledge_base,
                    source_name,
                    source_type,
                    payload_text,
                    metadata_json,
                    status,
                    retry_count,
                    max_retries,
                    next_attempt_at,
                    last_error,
                    document_id,
                    chunk_count,
                    queue_backend,
                    idempotency_key,
                    trace_id,
                    attempt_count,
                    started_at,
                    finished_at,
                    dead_lettered,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.knowledge_base,
                    job.source_name,
                    job.source_type,
                    job.payload_text,
                    json.dumps(job.metadata, ensure_ascii=True),
                    job.status,
                    job.retry_count,
                    job.max_retries,
                    job.next_attempt_at,
                    job.last_error,
                    job.document_id,
                    job.chunk_count,
                    job.queue_backend,
                    job.idempotency_key,
                    job.trace_id,
                    job.attempt_count,
                    job.started_at,
                    job.finished_at,
                    0,
                    job.created_at,
                    job.updated_at,
                ),
            )
        return job

    def get_by_idempotency_key(
        self,
        *,
        knowledge_base: str,
        idempotency_key: str,
    ) -> IngestionJob | None:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM ingestion_jobs
                WHERE knowledge_base = ? AND idempotency_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (knowledge_base, idempotency_key),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def claim_next_job(self) -> IngestionJob | None:
        now_iso = _to_iso(_utcnow())
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                SELECT *
                FROM ingestion_jobs
                WHERE status = ? AND next_attempt_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (STATUS_QUEUED, now_iso),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._claim_selected_row(cursor=cursor, row=row, now_iso=now_iso)

    def claim_job_by_id(self, job_id: str) -> IngestionJob | None:
        now_iso = _to_iso(_utcnow())
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                SELECT *
                FROM ingestion_jobs
                WHERE id = ? AND status = ?
                """,
                (job_id, STATUS_QUEUED),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._claim_selected_row(cursor=cursor, row=row, now_iso=now_iso)

    def _claim_selected_row(self, *, cursor: object, row: object, now_iso: str) -> IngestionJob:
        job_id = str(row["id"])
        attempt_count = int(row["attempt_count"]) + 1
        cursor.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?,
                attempt_count = ?,
                started_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (STATUS_PROCESSING, attempt_count, now_iso, now_iso, job_id),
        )
        cursor.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,))
        claimed = cursor.fetchone()
        if claimed is None:
            return None
        return _row_to_job(claimed)

    def mark_job_succeeded(
        self,
        *,
        job_id: str,
        document_id: str,
        chunk_count: int,
    ) -> IngestionJob:
        now_iso = _to_iso(_utcnow())
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?,
                    document_id = ?,
                    chunk_count = ?,
                    last_error = '',
                    finished_at = ?,
                    dead_lettered = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (STATUS_SUCCEEDED, document_id, chunk_count, now_iso, now_iso, job_id),
            )
            cursor.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Ingestion job not found: {job_id}")
        return _row_to_job(row)

    def mark_job_failed(
        self,
        *,
        job_id: str,
        error: str,
        base_backoff_seconds: int,
        max_backoff_seconds: int,
    ) -> IngestionJob:
        now = _utcnow()
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,))
            current = cursor.fetchone()
            if current is None:
                raise ValueError(f"Ingestion job not found: {job_id}")

            retry_count = int(current["retry_count"]) + 1
            max_retries = int(current["max_retries"])
            sanitized_error = _sanitize_error(error)
            next_status = STATUS_FAILED
            next_attempt_at = _to_iso(now)
            finished_at = _to_iso(now)
            dead_lettered = 1

            if retry_count <= max_retries:
                delay_seconds = min(
                    max_backoff_seconds,
                    max(base_backoff_seconds, 0) * (2 ** max(retry_count - 1, 0)),
                )
                next_status = STATUS_QUEUED
                next_attempt_at = _to_iso(now + timedelta(seconds=delay_seconds))
                finished_at = None
                dead_lettered = 0

            cursor.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?,
                    retry_count = ?,
                    next_attempt_at = ?,
                    last_error = ?,
                    finished_at = ?,
                    dead_lettered = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    retry_count,
                    next_attempt_at,
                    sanitized_error,
                    finished_at,
                    dead_lettered,
                    _to_iso(now),
                    job_id,
                ),
            )
            cursor.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Ingestion job not found: {job_id}")
        return _row_to_job(row)

    def get_job(self, job_id: str) -> IngestionJob | None:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[IngestionJob]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            if status:
                cursor.execute(
                    """
                    SELECT *
                    FROM ingestion_jobs
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT *
                    FROM ingestion_jobs
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
        return [_row_to_job(row) for row in rows]

    def count_open_jobs(self) -> int:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM ingestion_jobs
                WHERE status NOT IN (?, ?)
                """,
                (STATUS_SUCCEEDED, STATUS_FAILED),
            )
            row = cursor.fetchone()
        return int(row["total"]) if row is not None else 0


def _row_to_job(row: object) -> IngestionJob:
    data = dict(row)
    return IngestionJob(
        id=str(data["id"]),
        knowledge_base=str(data["knowledge_base"]),
        source_name=str(data["source_name"]),
        source_type=str(data["source_type"]),
        payload_text=str(data["payload_text"]),
        metadata=json.loads(str(data["metadata_json"])),
        status=str(data["status"]),
        retry_count=int(data["retry_count"]),
        max_retries=int(data["max_retries"]),
        next_attempt_at=str(data["next_attempt_at"]),
        last_error=str(data["last_error"]),
        document_id=None if data["document_id"] is None else str(data["document_id"]),
        chunk_count=int(data["chunk_count"]),
        queue_backend=str(data.get("queue_backend", "sqlite")),
        idempotency_key=str(data.get("idempotency_key", "")),
        trace_id=str(data.get("trace_id", "")),
        attempt_count=int(data.get("attempt_count", 0)),
        started_at=None if data.get("started_at") is None else str(data["started_at"]),
        finished_at=None if data.get("finished_at") is None else str(data["finished_at"]),
        dead_lettered=bool(int(data.get("dead_lettered", 0))),
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
    )
