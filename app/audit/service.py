"""Audit logging orchestration."""

from __future__ import annotations

from fastapi import Request

from app.auth.models import AuthContext
from app.core.request_context import get_request_context
from app.storage.audit_repository import AuditRepository


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    def record(
        self,
        *,
        principal: AuthContext,
        action: str,
        resource_type: str,
        resource_id: str = "",
        status: str = "success",
        detail: dict[str, object] | None = None,
        request: Request | None = None,
        session_id: str = "",
        job_id: str = "",
    ) -> int:
        context = get_request_context()
        ip_address = ""
        if request is not None and request.client is not None:
            ip_address = str(request.client.host)
        return self.repository.add_event(
            actor_type=principal.actor_type,
            actor_id=principal.actor_id,
            actor_name=principal.actor_name,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            detail=detail,
            request_id=context.request_id,
            trace_id=context.trace_id,
            session_id=session_id or context.session_id,
            job_id=job_id or context.job_id,
            ip=ip_address,
        )
