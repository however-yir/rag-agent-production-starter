"""FastAPI routes."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import PlainTextResponse

from app.agent.service import ReActAgentService
from app.api.dependencies import (
    get_audit_service,
    get_audit_repository,
    get_current_principal,
    get_ingestion_service,
    get_session_repository,
    get_settings,
    get_vector_repository,
    require_permissions,
)
from app.api.schemas import (
    AuditLogResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    IngestionJobResponse,
    IngestionProcessResponse,
    IngestionTextRequest,
    MetricsResponse,
    SessionCreateResponse,
    SessionMessageResponse,
    SessionResponse,
)
from app.audit.service import AuditService
from app.auth.models import AuthContext
from app.core.request_context import RequestContext, get_request_context, set_request_context
from app.core.settings import AppSettings
from app.ingestion.service import IngestionService
from app.observability.metrics import get_metrics_registry
from app.rag.service import PolicySearchService
from app.storage.audit_repository import AuditRepository
from app.storage.session_repository import SessionRepository
from app.storage.vector_repository import VectorRepository

router = APIRouter()

try:  # pragma: no cover - environment-dependent
    import multipart  # type: ignore  # noqa: F401

    MULTIPART_ENABLED = True
except Exception:  # pragma: no cover - environment-dependent
    MULTIPART_ENABLED = False


@router.get("/health", response_model=HealthResponse)
def health(settings: AppSettings = Depends(get_settings)) -> HealthResponse:
    mode = "mock" if settings.use_mock_services else "live"
    return HealthResponse(status="ok", mode=mode, version="0.3.0")


@router.get("/chat/examples", response_model=list[str])
def chat_examples(settings: AppSettings = Depends(get_settings)) -> list[str]:
    return list(settings.default_queries)


@router.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_permissions("chat:write"))],
)
def chat(
    payload: ChatRequest,
    request: Request,
    settings: AppSettings = Depends(get_settings),
    vector_repository: VectorRepository = Depends(get_vector_repository),
    session_repository: SessionRepository = Depends(get_session_repository),
    audit_service: AuditService = Depends(get_audit_service),
    principal: AuthContext = Depends(get_current_principal),
) -> ChatResponse:
    runtime_settings = settings
    if payload.mode == "mock" and not settings.use_mock_services:
        runtime_settings = replace(settings, use_mock_services=True)

    policy_service = PolicySearchService(
        settings=runtime_settings,
        vector_repository=vector_repository,
    )
    agent_service = ReActAgentService(
        runtime_settings,
        policy_service=policy_service,
    )

    session_id = payload.session_id or session_repository.create_session()
    history = session_repository.get_messages(session_id)
    context = get_request_context()
    set_request_context(
        RequestContext(
            request_id=context.request_id,
            trace_id=context.trace_id,
            session_id=session_id,
            job_id=context.job_id,
            actor_id=context.actor_id,
            actor_type=context.actor_type,
        )
    )
    session_repository.add_message(
        session_id=session_id,
        role="user",
        content=payload.query,
    )

    response = agent_service.answer(
        payload.query,
        knowledge_base=payload.knowledge_base,
        session_history=history,
    )
    assistant_latency = float(response.metadata.get("latency_ms", 0.0))
    session_repository.add_message(
        session_id=session_id,
        role="assistant",
        content=response.answer,
        route=response.route,
        latency_ms=assistant_latency,
        metadata={
            "evidence_count": len(response.evidence),
            "tool_calls": [item.to_dict() for item in response.tool_calls],
        },
    )
    response.metadata["session_id"] = session_id
    audit_service.record(
        principal=principal,
        action="chat.answer",
        resource_type="session",
        resource_id=session_id,
        detail={
            "route": response.route,
            "knowledge_base": payload.knowledge_base,
            "evidence_count": len(response.evidence),
        },
        request=request,
        session_id=session_id,
    )
    return ChatResponse.from_agent_response(response)


@router.post(
    "/ingestion/text",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permissions("ingestion:write"))],
)
def ingest_text(
    payload: IngestionTextRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ingestion_service: IngestionService = Depends(get_ingestion_service),
    audit_service: AuditService = Depends(get_audit_service),
    principal: AuthContext = Depends(get_current_principal),
) -> IngestionJobResponse:
    context = get_request_context()
    job = ingestion_service.enqueue_text(
        knowledge_base=payload.knowledge_base,
        source_name=payload.source_name,
        text=payload.text,
        idempotency_key=idempotency_key or "",
        trace_id=context.trace_id,
    )
    audit_service.record(
        principal=principal,
        action="ingestion.enqueue_text",
        resource_type="ingestion_job",
        resource_id=job.id,
        detail={
            "knowledge_base": payload.knowledge_base,
            "source_name": payload.source_name,
            "queue_backend": job.queue_backend,
        },
        request=request,
        job_id=job.id,
    )
    return IngestionJobResponse.model_validate(job.to_dict())


if MULTIPART_ENABLED:

    @router.post(
        "/ingestion/upload",
        response_model=IngestionJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_permissions("ingestion:write"))],
    )
    async def ingest_upload(
        request: Request,
        file: UploadFile = File(...),
        knowledge_base: str = Form("default"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ingestion_service: IngestionService = Depends(get_ingestion_service),
        audit_service: AuditService = Depends(get_audit_service),
        principal: AuthContext = Depends(get_current_principal),
    ) -> IngestionJobResponse:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        context = get_request_context()
        job = ingestion_service.enqueue_upload(
            knowledge_base=knowledge_base,
            filename=file.filename or "uploaded_file",
            file_bytes=file_bytes,
            idempotency_key=idempotency_key or "",
            trace_id=context.trace_id,
        )
        audit_service.record(
            principal=principal,
            action="ingestion.enqueue_upload",
            resource_type="ingestion_job",
            resource_id=job.id,
            detail={
                "knowledge_base": knowledge_base,
                "filename": file.filename or "uploaded_file",
                "queue_backend": job.queue_backend,
            },
            request=request,
            job_id=job.id,
        )
        return IngestionJobResponse.model_validate(job.to_dict())

else:

    @router.post(
        "/ingestion/upload",
        response_model=IngestionJobResponse,
        dependencies=[Depends(require_permissions("ingestion:write"))],
    )
    async def ingest_upload() -> IngestionJobResponse:
        raise HTTPException(
            status_code=503,
            detail=(
                "Multipart upload support is unavailable. Install "
                "`python-multipart` to enable `/ingestion/upload`."
            ),
        )


@router.get(
    "/ingestion/jobs",
    response_model=list[IngestionJobResponse],
    dependencies=[Depends(require_permissions("ingestion:read"))],
)
def list_ingestion_jobs(
    status: str | None = None,
    limit: int = 100,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> list[IngestionJobResponse]:
    jobs = ingestion_service.list_jobs(status=status, limit=limit)
    return [IngestionJobResponse.model_validate(item.to_dict()) for item in jobs]


@router.get(
    "/ingestion/jobs/{job_id}",
    response_model=IngestionJobResponse,
    dependencies=[Depends(require_permissions("ingestion:read"))],
)
def get_ingestion_job(
    job_id: str,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> IngestionJobResponse:
    job = ingestion_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Ingestion job not found: {job_id}")
    return IngestionJobResponse.model_validate(job.to_dict())


@router.post(
    "/ingestion/jobs/process",
    response_model=IngestionProcessResponse,
    dependencies=[Depends(require_permissions("ingestion:process"))],
)
def process_ingestion_jobs(
    max_jobs: int = 20,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> IngestionProcessResponse:
    processed = ingestion_service.process_jobs(max_jobs=max_jobs)
    return IngestionProcessResponse(processed=processed)


@router.get(
    "/ingestion/documents",
    response_model=list[dict[str, Any]],
    dependencies=[Depends(require_permissions("ingestion:read"))],
)
def list_ingested_documents(
    knowledge_base: str | None = None,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> list[dict[str, Any]]:
    return ingestion_service.list_documents(knowledge_base=knowledge_base)


@router.post(
    "/sessions",
    response_model=SessionCreateResponse,
    dependencies=[Depends(require_permissions("sessions:write"))],
)
def create_session(
    session_repository: SessionRepository = Depends(get_session_repository),
) -> SessionCreateResponse:
    return SessionCreateResponse(session_id=session_repository.create_session())


@router.get(
    "/sessions",
    response_model=list[SessionResponse],
    dependencies=[Depends(require_permissions("sessions:read"))],
)
def list_sessions(
    limit: int = 50,
    session_repository: SessionRepository = Depends(get_session_repository),
) -> list[SessionResponse]:
    rows = session_repository.list_sessions(limit=limit)
    return [
        SessionResponse.model_validate(
            {
                "session_id": item["id"],
                "created_at": item["created_at"],
                "message_count": item["message_count"],
            }
        )
        for item in rows
    ]


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[SessionMessageResponse],
    dependencies=[Depends(require_permissions("sessions:read"))],
)
def list_session_messages(
    session_id: str,
    limit: int = 200,
    session_repository: SessionRepository = Depends(get_session_repository),
) -> list[SessionMessageResponse]:
    rows = session_repository.get_messages(session_id=session_id, limit=limit)
    return [SessionMessageResponse.model_validate(item) for item in rows]


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    dependencies=[Depends(require_permissions("metrics:read"))],
)
def metrics() -> MetricsResponse:
    snapshot = get_metrics_registry().snapshot()
    return MetricsResponse.model_validate(snapshot)


@router.get(
    "/metrics/prometheus",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_permissions("metrics:read"))],
)
def metrics_prometheus() -> PlainTextResponse:
    payload = get_metrics_registry().prometheus_text()
    return PlainTextResponse(payload)


@router.get(
    "/admin/audit-logs",
    response_model=list[AuditLogResponse],
    dependencies=[Depends(require_permissions("audit:read"))],
)
def list_audit_logs(
    limit: int = 200,
    repository: AuditRepository = Depends(get_audit_repository),
) -> list[AuditLogResponse]:
    rows = repository.list_events(limit=limit)
    return [AuditLogResponse.model_validate(item) for item in rows]
