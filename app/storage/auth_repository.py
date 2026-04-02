"""Authentication and RBAC persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import uuid

from app.storage.database import Database


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class UserRecord:
    id: str
    username: str
    password_hash: str
    is_active: bool
    created_at: str


@dataclass(slots=True)
class ApiKeyRecord:
    id: str
    user_id: str
    name: str
    key_prefix: str
    key_hash: str
    is_active: bool
    last_used_at: str | None
    created_at: str


class AuthRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_user(row)

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_user(row)

    def create_user(self, *, username: str, password_hash: str) -> UserRecord:
        user = UserRecord(
            id=str(uuid.uuid4()),
            username=username,
            password_hash=password_hash,
            is_active=True,
            created_at=_utcnow(),
        )
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO users (id, username, password_hash, is_active, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.username,
                    user.password_hash,
                    1,
                    user.created_at,
                ),
            )
        return user

    def assign_role(self, *, user_id: str, role_name: str) -> None:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
            role_row = cursor.fetchone()
            if role_row is None:
                raise ValueError(f"Unknown role: {role_name}")
            cursor.execute(
                """
                INSERT OR IGNORE INTO user_roles (user_id, role_id, created_at)
                VALUES (?, ?, ?)
                """,
                (user_id, str(role_row["id"]), _utcnow()),
            )

    def list_roles(self, *, user_id: str) -> list[str]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT r.name
                FROM user_roles ur
                JOIN roles r ON ur.role_id = r.id
                WHERE ur.user_id = ?
                ORDER BY r.name ASC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return [str(row["name"]) for row in rows]

    def list_permissions(self, *, user_id: str) -> list[str]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT DISTINCT p.code
                FROM user_roles ur
                JOIN role_permissions rp ON ur.role_id = rp.role_id
                JOIN permissions p ON rp.permission_id = p.id
                WHERE ur.user_id = ?
                ORDER BY p.code ASC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return [str(row["code"]) for row in rows]

    def create_api_key(self, *, user_id: str, name: str, raw_key: str) -> ApiKeyRecord:
        api_key = ApiKeyRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            key_prefix=raw_key[:10],
            key_hash=hash_api_key(raw_key),
            is_active=True,
            last_used_at=None,
            created_at=_utcnow(),
        )
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO api_keys (
                    id, user_id, name, key_prefix, key_hash, is_active, last_used_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    api_key.id,
                    api_key.user_id,
                    api_key.name,
                    api_key.key_prefix,
                    api_key.key_hash,
                    1,
                    None,
                    api_key.created_at,
                ),
            )
        return api_key

    def get_api_key(self, *, raw_key: str) -> ApiKeyRecord | None:
        hashed = hash_api_key(raw_key)
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM api_keys
                WHERE key_hash = ? AND is_active = 1
                """,
                (hashed,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_api_key(row)

    def touch_api_key(self, *, api_key_id: str) -> None:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (_utcnow(), api_key_id),
            )


def _row_to_user(row: object) -> UserRecord:
    data = dict(row)
    return UserRecord(
        id=str(data["id"]),
        username=str(data["username"]),
        password_hash=str(data["password_hash"]),
        is_active=bool(int(data["is_active"])),
        created_at=str(data["created_at"]),
    )


def _row_to_api_key(row: object) -> ApiKeyRecord:
    data = dict(row)
    return ApiKeyRecord(
        id=str(data["id"]),
        user_id=str(data["user_id"]),
        name=str(data["name"]),
        key_prefix=str(data["key_prefix"]),
        key_hash=str(data["key_hash"]),
        is_active=bool(int(data["is_active"])),
        last_used_at=None if data["last_used_at"] is None else str(data["last_used_at"]),
        created_at=str(data["created_at"]),
    )
