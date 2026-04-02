"""Background worker for queued ingestion jobs."""

from __future__ import annotations

from threading import Event, Thread

from app.core.logging import get_logger
from app.ingestion.service import IngestionService

logger = get_logger(__name__)


class IngestionWorker:
    def __init__(
        self,
        *,
        ingestion_service: IngestionService,
        poll_interval_seconds: float = 1.0,
        max_jobs_per_tick: int = 5,
    ) -> None:
        self.ingestion_service = ingestion_service
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)
        self.max_jobs_per_tick = max(1, max_jobs_per_tick)
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run_loop, name="ingestion-worker", daemon=True)
        self._thread.start()
        logger.info(
            "INGESTION_WORKER_STARTED | poll_interval=%.2f | max_jobs_per_tick=%s",
            self.poll_interval_seconds,
            self.max_jobs_per_tick,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("INGESTION_WORKER_STOPPED")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            processed = self.ingestion_service.process_jobs(max_jobs=self.max_jobs_per_tick)
            if processed == 0:
                self._stop_event.wait(self.poll_interval_seconds)
