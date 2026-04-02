"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
import time
import uuid

from fastapi import FastAPI, Request

from app.api.auth_routes import router as auth_router
from app.api.dependencies import get_ingestion_service_singleton, get_settings
from app.api.routes import router
from app.core.logging import configure_logging
from app.core.request_context import RequestContext, clear_request_context, set_request_context
from app.ingestion.worker import IngestionWorker
from app.observability.metrics import get_metrics_registry
from app.observability.telemetry import (
    resolve_trace_id,
    setup_open_telemetry,
    start_span,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    setup_open_telemetry(settings)
    worker = None
    if settings.ingestion_embedded_worker_enabled:
        worker = IngestionWorker(
            ingestion_service=get_ingestion_service_singleton(),
            poll_interval_seconds=settings.ingestion_worker_poll_seconds,
            max_jobs_per_tick=settings.ingestion_worker_batch_size,
        )
        worker.start()
    application.state.ingestion_worker = worker
    try:
        yield
    finally:
        if worker is not None:
            worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, use_json=settings.log_json)

    application = FastAPI(
        title="RAG ReAct Agent Heavyweight Foundation",
        version="0.3.0",
        description=(
            "A modularized foundation for growing the original RAG + ReAct demo into "
            "a service-oriented, testable, and deployable agent application."
        ),
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def attach_request_context(request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        status_code = 500
        with start_span(
            "http.request",
            kind="server",
            attributes={
                "http.method": request.method,
                "http.path": request.url.path,
                "request_id": request_id,
            },
        ) as span:
            trace_id = request.headers.get("X-Trace-Id") or resolve_trace_id()
            set_request_context(RequestContext(request_id=request_id, trace_id=trace_id))
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers["X-Request-Id"] = request_id
                response.headers["X-Trace-Id"] = trace_id
                return response
            finally:
                latency_ms = (time.perf_counter() - started) * 1000.0
                get_metrics_registry().record_http_request(
                    method=request.method,
                    path=request.url.path,
                    status_code=status_code,
                    latency_ms=latency_ms,
                )
                if span is not None:
                    span.set_attribute("http.status_code", status_code)
                    span.set_attribute("http.latency_ms", round(latency_ms, 4))
                    span.set_attribute("trace_id", trace_id)
                clear_request_context()

    application.include_router(router)
    application.include_router(auth_router)
    return application


app = create_app()
