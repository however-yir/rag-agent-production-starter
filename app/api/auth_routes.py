"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    get_audit_service,
    get_auth_service,
    get_current_principal,
    require_permissions,
)
from app.api.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    LoginRequest,
    LoginResponse,
    PrincipalResponse,
)
from app.audit.service import AuditService
from app.auth.models import AuthContext
from app.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> LoginResponse:
    result = auth_service.login_with_password(
        username=payload.username,
        password=payload.password,
    )
    audit_service.record(
        principal=result.context,
        action="auth.login",
        resource_type="token",
        status="success",
        detail={"username": payload.username},
        request=request,
    )
    return LoginResponse(
        access_token=result.access_token,
        token_type=result.token_type,
        expires_in_seconds=result.expires_in_seconds,
        principal=PrincipalResponse.model_validate(
            {
                "actor_id": result.context.actor_id,
                "actor_name": result.context.actor_name,
                "actor_type": result.context.actor_type,
                "roles": result.context.roles,
                "permissions": result.context.permissions,
                "auth_type": result.context.auth_type,
            }
        ),
    )


@router.get("/me", response_model=PrincipalResponse)
def me(principal: AuthContext = Depends(get_current_principal)) -> PrincipalResponse:
    return PrincipalResponse.model_validate(
        {
            "actor_id": principal.actor_id,
            "actor_name": principal.actor_name,
            "actor_type": principal.actor_type,
            "roles": principal.roles,
            "permissions": principal.permissions,
            "auth_type": principal.auth_type,
        }
    )


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    dependencies=[Depends(require_permissions("auth:manage_api_keys"))],
)
def create_api_key(
    payload: ApiKeyCreateRequest,
    request: Request,
    principal: AuthContext = Depends(get_current_principal),
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> ApiKeyCreateResponse:
    created = auth_service.create_api_key(actor_id=principal.actor_id, name=payload.name)
    audit_service.record(
        principal=principal,
        action="auth.create_api_key",
        resource_type="api_key",
        resource_id=created["id"],
        status="success",
        detail={"name": payload.name, "prefix": created["key_prefix"]},
        request=request,
    )
    return ApiKeyCreateResponse.model_validate(created)
