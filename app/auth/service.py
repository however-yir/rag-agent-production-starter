"""Authentication, token, and RBAC service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import secrets

from app.auth.jwt_utils import decode_hs256, encode_hs256, issue_access_token_payload
from app.auth.models import AuthContext
from app.core.settings import AppSettings
from app.storage.auth_repository import AuthRepository


@dataclass(slots=True)
class LoginResult:
    access_token: str
    token_type: str
    expires_in_seconds: int
    context: AuthContext


class AuthService:
    def __init__(self, settings: AppSettings, repository: AuthRepository) -> None:
        self.settings = settings
        self.repository = repository
        self._bootstrap_admin()

    def login_with_password(self, *, username: str, password: str) -> LoginResult:
        user = self.repository.get_user_by_username(username)
        if user is None or not user.is_active:
            raise ValueError("Invalid credentials.")
        if not verify_password(password=password, password_hash=user.password_hash):
            raise ValueError("Invalid credentials.")

        roles = self.repository.list_roles(user_id=user.id)
        permissions = self.repository.list_permissions(user_id=user.id)
        context = AuthContext(
            actor_id=user.id,
            actor_name=user.username,
            actor_type="user",
            roles=roles,
            permissions=permissions,
            auth_type="jwt",
        )
        payload = issue_access_token_payload(
            subject=user.id,
            username=user.username,
            roles=roles,
            permissions=permissions,
            issuer=self.settings.jwt_issuer,
            expires_in_minutes=self.settings.jwt_access_token_exp_minutes,
        )
        token = encode_hs256(payload, secret=self.settings.jwt_secret)
        return LoginResult(
            access_token=token,
            token_type="bearer",
            expires_in_seconds=self.settings.jwt_access_token_exp_minutes * 60,
            context=context,
        )

    def authenticate_bearer_token(self, token: str) -> AuthContext:
        payload = decode_hs256(token, secret=self.settings.jwt_secret)
        if payload.get("iss") != self.settings.jwt_issuer:
            raise ValueError("Invalid token issuer.")
        actor_id = str(payload.get("sub", ""))
        username = str(payload.get("username", ""))
        if not actor_id:
            raise ValueError("Token subject is missing.")
        user = self.repository.get_user_by_id(actor_id)
        if user is None or not user.is_active:
            raise ValueError("User is inactive.")
        return AuthContext(
            actor_id=actor_id,
            actor_name=username or user.username,
            actor_type="user",
            roles=[str(item) for item in payload.get("roles", [])],
            permissions=[str(item) for item in payload.get("permissions", [])],
            auth_type="jwt",
        )

    def authenticate_api_key(self, raw_key: str) -> AuthContext:
        api_key = self.repository.get_api_key(raw_key=raw_key)
        if api_key is None or not api_key.is_active:
            raise ValueError("Invalid API key.")
        user = self.repository.get_user_by_id(api_key.user_id)
        if user is None or not user.is_active:
            raise ValueError("API key user is inactive.")
        self.repository.touch_api_key(api_key_id=api_key.id)
        return AuthContext(
            actor_id=user.id,
            actor_name=user.username,
            actor_type="api_key",
            roles=self.repository.list_roles(user_id=user.id),
            permissions=self.repository.list_permissions(user_id=user.id),
            auth_type="api_key",
            api_key_id=api_key.id,
        )

    def create_api_key(self, *, actor_id: str, name: str = "default") -> dict[str, str]:
        raw_key = f"rag_{secrets.token_urlsafe(24)}"
        api_key = self.repository.create_api_key(
            user_id=actor_id,
            name=name,
            raw_key=raw_key,
        )
        return {
            "id": api_key.id,
            "name": api_key.name,
            "key_prefix": api_key.key_prefix,
            "api_key": raw_key,
        }

    def _bootstrap_admin(self) -> None:
        username = self.settings.bootstrap_admin_username.strip()
        if not username:
            return
        user = self.repository.get_user_by_username(username)
        if user is None:
            created = self.repository.create_user(
                username=username,
                password_hash=hash_password(self.settings.bootstrap_admin_password),
            )
            user_id = created.id
        else:
            user_id = user.id
        self.repository.assign_role(user_id=user_id, role_name="admin")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(*, password: str, password_hash: str) -> bool:
    try:
        _, iterations_raw, salt, digest = password_hash.split("$", maxsplit=3)
        iterations = int(iterations_raw)
    except Exception:
        return False
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return secrets.compare_digest(expected, digest)


def utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())
