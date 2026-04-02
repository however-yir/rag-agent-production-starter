"""Ingestion services."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import time

from app.core.logging import get_logger
from app.core.request_context import RequestContext, get_request_context, set_request_context
from app.core.settings import AppSettings
from app.ingestion.chunker import chunk_text
from app.ingestion.extractors import extract_text_from_upload
from app.observability.metrics import get_metrics_registry
from app.observability.telemetry import start_span
from app.queue.base import QueueBackend
from app.queue.factory import build_queue_backend
from app.rag.embeddings import Embedder, get_embedder
from app.storage.ingestion_job_repository import IngestionJob, IngestionJobRepository
from app.storage.vector_repository import IngestedDocument, VectorRepository

logger = get_logger(__name__)


@dataclass(slots=True)
class IngestionResult:
    document_id: str
    knowledge_base: str
    source_name: str
    source_type: str
    chunk_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class IngestionService:
    """Uploads, chunks, vectorizes, and stores documents, now with async queue support."""

    def __init__(
        self,
        settings: AppSettings,
        repository: VectorRepository,
        job_repository: IngestionJobRepository | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.job_repository = job_repository
        self.embedder = embedder or get_embedder(settings)
        self.queue_backend: QueueBackend = build_queue_backend(settings)

    def ingest_text(
        self,
        *,
        knowledge_base: str,
        source_name: str,
        text: str,
        source_type: str = "text",
        metadata: dict[str, str] | None = None,
    ) -> IngestionResult:
        return self._ingest_text_now(
            knowledge_base=knowledge_base,
            source_name=source_name,
            text=text,
            source_type=source_type,
            metadata=metadata,
        )

    def ingest_upload(
        self,
        *,
        knowledge_base: str,
        filename: str,
        file_bytes: bytes,
    ) -> IngestionResult:
        source_type, text = extract_text_from_upload(filename=filename, file_bytes=file_bytes)
        return self._ingest_text_now(
            knowledge_base=knowledge_base,
            source_name=filename,
            text=text,
            source_type=source_type,
            metadata={"filename": filename},
        )

    def list_documents(self, knowledge_base: str | None = None) -> list[dict[str, object]]:
        return self.repository.list_documents(knowledge_base=knowledge_base)

    def enqueue_text(
        self,
        *,
        knowledge_base: str,
        source_name: str,
        text: str,
        source_type: str = "text",
        metadata: dict[str, str] | None = None,
        max_retries: int | None = None,
        idempotency_key: str = "",
        trace_id: str = "",
    ) -> IngestionJob:
        repository = self._require_job_repository()
        if idempotency_key.strip():
            existing = repository.get_by_idempotency_key(
                knowledge_base=knowledge_base,
                idempotency_key=idempotency_key.strip(),
            )
            if existing is not None:
                return existing
        job = repository.enqueue_text_job(
            knowledge_base=knowledge_base,
            source_name=source_name,
            source_type=source_type,
            payload_text=text,
            metadata=metadata,
            max_retries=max_retries
            if max_retries is not None
            else self.settings.ingestion_max_retries,
            queue_backend=self.queue_backend.name,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )
        self.queue_backend.enqueue(job_id=job.id, available_at=_from_iso(job.next_attempt_at))
        return job

    def enqueue_upload(
        self,
        *,
        knowledge_base: str,
        filename: str,
        file_bytes: bytes,
        max_retries: int | None = None,
        idempotency_key: str = "",
        trace_id: str = "",
    ) -> IngestionJob:
        source_type, text = extract_text_from_upload(filename=filename, file_bytes=file_bytes)
        return self.enqueue_text(
            knowledge_base=knowledge_base,
            source_name=filename,
            text=text,
            source_type=source_type,
            metadata={"filename": filename},
            max_retries=max_retries,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )

    def get_job(self, job_id: str) -> IngestionJob | None:
        repository = self._require_job_repository()
        return repository.get_job(job_id)

    def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[IngestionJob]:
        repository = self._require_job_repository()
        return repository.list_jobs(status=status, limit=limit)

    def process_next_job(self) -> IngestionJob | None:
        repository = self._require_job_repository()
        claimed = self._claim_next_job(repository)
        if claimed is None:
            return None

        context = get_request_context()
        set_request_context(
            RequestContext(
                request_id=context.request_id,
                trace_id=claimed.trace_id or context.trace_id,
                session_id=context.session_id,
                job_id=claimed.id,
                actor_id=context.actor_id,
                actor_type=context.actor_type,
            )
        )
        metrics = get_metrics_registry()
        started = time.perf_counter()
        with start_span(
            "ingestion.process_job",
            kind="consumer",
            attributes={
                "job.id": claimed.id,
                "job.knowledge_base": claimed.knowledge_base,
                "job.backend": claimed.queue_backend,
                "job.attempt": claimed.attempt_count,
            },
        ):
            try:
                result = self._ingest_text_now(
                    knowledge_base=claimed.knowledge_base,
                    source_name=claimed.source_name,
                    text=claimed.payload_text,
                    source_type=claimed.source_type,
                    metadata=claimed.metadata,
                )
                completed = repository.mark_job_succeeded(
                    job_id=claimed.id,
                    document_id=result.document_id,
                    chunk_count=result.chunk_count,
                )
                self.queue_backend.ack(job_id=completed.id)
                metrics.record_latency(
                    "ingestion_job_attempt",
                    (time.perf_counter() - started) * 1000.0,
                )
                metrics.record_ingestion_job(
                    status=completed.status,
                    backend=completed.queue_backend,
                )
                logger.info(
                    (
                        "INGESTION_JOB_SUCCEEDED | job_id=%s | kb=%s | source=%s | "
                        "chunks=%s | backend=%s | attempts=%s"
                    ),
                    completed.id,
                    completed.knowledge_base,
                    completed.source_name,
                    completed.chunk_count,
                    completed.queue_backend,
                    completed.attempt_count,
                )
                return completed
            except Exception as exc:
                failed = repository.mark_job_failed(
                    job_id=claimed.id,
                    error=str(exc),
                    base_backoff_seconds=self.settings.ingestion_retry_backoff_seconds,
                    max_backoff_seconds=self.settings.ingestion_retry_max_backoff_seconds,
                )
                self.queue_backend.nack(
                    job_id=failed.id,
                    retry_at=_from_iso(failed.next_attempt_at),
                    dead_letter=failed.dead_lettered,
                )
                metrics.record_latency(
                    "ingestion_job_attempt",
                    (time.perf_counter() - started) * 1000.0,
                )
                metrics.record_ingestion_job(
                    status=failed.status,
                    backend=failed.queue_backend,
                )
                logger.warning(
                    (
                        "INGESTION_JOB_FAILED | job_id=%s | retry=%s/%s | status=%s | "
                        "backend=%s | dead_lettered=%s | error=%s"
                    ),
                    failed.id,
                    failed.retry_count,
                    failed.max_retries,
                    failed.status,
                    failed.queue_backend,
                    failed.dead_lettered,
                    failed.last_error,
                )
                return failed

    def process_jobs(self, *, max_jobs: int = 10) -> int:
        if max_jobs <= 0:
            return 0
        processed = 0
        for _ in range(max_jobs):
            job = self.process_next_job()
            if job is None:
                break
            processed += 1
        return processed

    def run_until_idle(self, *, max_iterations: int = 100, jobs_per_iteration: int = 20) -> int:
        total_processed = 0
        for _ in range(max_iterations):
            processed = self.process_jobs(max_jobs=jobs_per_iteration)
            total_processed += processed
            if processed == 0:
                break
        return total_processed

    def _ingest_text_now(
        self,
        *,
        knowledge_base: str,
        source_name: str,
        text: str,
        source_type: str = "text",
        metadata: dict[str, str] | None = None,
    ) -> IngestionResult:
        started = time.perf_counter()
        with start_span(
            "ingestion.ingest_text_now",
            attributes={
                "ingestion.knowledge_base": knowledge_base,
                "ingestion.source_name": source_name,
                "ingestion.source_type": source_type,
            },
        ):
            chunks = chunk_text(
                text,
                chunk_size=self.settings.chunk_size,
                chunk_overlap=self.settings.chunk_overlap,
            )
            if not chunks:
                raise ValueError("No text content was extracted from the document.")

            embeddings = self.embedder.embed(chunks)
            document = self.repository.add_document_with_chunks(
                knowledge_base=knowledge_base,
                source_name=source_name,
                source_type=source_type,
                chunks=chunks,
                embeddings=embeddings,
                metadata=metadata,
            )
            result = _to_result(document)
            metrics = get_metrics_registry()
            metrics.record_latency("ingestion_total", (time.perf_counter() - started) * 1000.0)
            logger.info(
                "INGESTION_RESULT | kb=%s | source=%s | chunks=%s",
                knowledge_base,
                source_name,
                result.chunk_count,
            )
            return result

    def _require_job_repository(self) -> IngestionJobRepository:
        if self.job_repository is None:
            raise RuntimeError("Ingestion job repository is required for queue operations.")
        return self.job_repository

    def _claim_next_job(self, repository: IngestionJobRepository) -> IngestionJob | None:
        if self.queue_backend.name == "redis":
            queue_item = self.queue_backend.dequeue()
            if queue_item is None:
                return None
            claimed = repository.claim_job_by_id(queue_item.job_id)
            if claimed is not None:
                return claimed
        return repository.claim_next_job()


def _to_result(document: IngestedDocument) -> IngestionResult:
    return IngestionResult(
        document_id=document.id,
        knowledge_base=document.knowledge_base,
        source_name=document.source_name,
        source_type=document.source_type,
        chunk_count=document.chunk_count,
    )


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
