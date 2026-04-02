"""Queue backend factory."""

from __future__ import annotations

from app.core.logging import get_logger
from app.core.settings import AppSettings
from app.queue.base import QueueBackend
from app.queue.redis_queue import RedisQueueBackend
from app.queue.sqlite_queue import SqliteQueueBackend

logger = get_logger(__name__)


def build_queue_backend(settings: AppSettings) -> QueueBackend:
    backend = settings.ingestion_queue_backend.strip().lower()
    if backend == "redis":
        if not settings.redis_url:
            logger.warning(
                "QUEUE_BACKEND_FALLBACK | requested=redis | reason=missing REDIS_URL | using=sqlite"
            )
            return SqliteQueueBackend()
        try:
            return RedisQueueBackend(settings.redis_url)
        except Exception as exc:
            logger.warning(
                "QUEUE_BACKEND_FALLBACK | requested=redis | reason=%s | using=sqlite",
                exc,
            )
            return SqliteQueueBackend()
    return SqliteQueueBackend()
