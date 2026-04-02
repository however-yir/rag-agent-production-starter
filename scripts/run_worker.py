#!/usr/bin/env python3
"""Run ingestion queue worker as a standalone process."""

from __future__ import annotations

from pathlib import Path
import signal
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.dependencies import get_ingestion_service_singleton, get_settings
from app.core.logging import configure_logging, get_logger
from app.ingestion.worker import IngestionWorker

logger = get_logger(__name__)
_RUNNING = True


def _handle_shutdown(signum, frame):  # type: ignore[no-untyped-def]
    _ = frame
    global _RUNNING
    logger.info("WORKER_SIGNAL_RECEIVED | signal=%s", signum)
    _RUNNING = False


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, use_json=settings.log_json)
    worker = IngestionWorker(
        ingestion_service=get_ingestion_service_singleton(),
        poll_interval_seconds=settings.ingestion_worker_poll_seconds,
        max_jobs_per_tick=settings.ingestion_worker_batch_size,
    )
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    worker.start()
    logger.info(
        "WORKER_STARTED | backend=%s | poll=%.2f | batch=%s",
        settings.ingestion_queue_backend,
        settings.ingestion_worker_poll_seconds,
        settings.ingestion_worker_batch_size,
    )
    try:
        while _RUNNING:
            time.sleep(0.2)
    finally:
        worker.stop()
        logger.info("WORKER_STOPPED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
