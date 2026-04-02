"""API schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.models import AgentResponse


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    mode: Literal["live", "mock"] = "live"
    session_id: str | None = None
    knowledge_base: str = "default"


class RetrievedDocumentResponse(BaseModel):
    identifier: str
    title: str
    content: str
    source: str
    score: float


class ToolInvocationResponse(BaseModel):
    name: str
    query: str
    output: str


class ChatResponse(BaseModel):
    answer: str
    route: str
    evidence: list[RetrievedDocumentResponse] = Field(default_factory=list)
    tool_calls: list[ToolInvocationResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_agent_response(cls, response: AgentResponse) -> "ChatResponse":
        return cls.model_validate(response.to_dict())


class HealthResponse(BaseModel):
    status: str
    mode: str
    version: str


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class PrincipalResponse(BaseModel):
    actor_id: str
    actor_name: str
    actor_type: str
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    auth_type: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in_seconds: int
    principal: PrincipalResponse


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="default", min_length=1)


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    api_key: str


class IngestionTextRequest(BaseModel):
    knowledge_base: str = "default"
    source_name: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)


class IngestionJobResponse(BaseModel):
    id: str
    knowledge_base: str
    source_name: str
    source_type: str
    metadata: dict[str, str] = Field(default_factory=dict)
    status: str
    retry_count: int
    max_retries: int
    next_attempt_at: str
    last_error: str
    document_id: str | None = None
    chunk_count: int
    queue_backend: str
    idempotency_key: str
    trace_id: str
    attempt_count: int
    started_at: str | None = None
    finished_at: str | None = None
    dead_lettered: bool
    created_at: str
    updated_at: str


class IngestionProcessResponse(BaseModel):
    processed: int


class SessionResponse(BaseModel):
    session_id: str
    created_at: str
    message_count: int


class SessionCreateResponse(BaseModel):
    session_id: str


class SessionMessageResponse(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    route: str
    latency_ms: float
    metadata: dict[str, Any]
    created_at: str


class MetricsResponse(BaseModel):
    routes: dict[str, int]
    rag: dict[str, Any]
    latency: dict[str, dict[str, float]]
    http: dict[str, Any] = Field(default_factory=dict)
    ingestion_jobs: dict[str, Any] = Field(default_factory=dict)
    prometheus_available: bool = False


class AuditLogResponse(BaseModel):
    id: int
    actor_type: str
    actor_id: str
    actor_name: str
    action: str
    resource_type: str
    resource_id: str
    status: str
    detail: dict[str, Any] = Field(default_factory=dict)
    request_id: str
    trace_id: str
    session_id: str
    job_id: str
    ip: str
    created_at: str
