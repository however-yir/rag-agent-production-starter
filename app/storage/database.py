"""SQLite database management."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from app.core.settings import AppSettings


class Database:
    """Lightweight SQLite wrapper with schema bootstrap."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.db_path = Path(settings.database_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    knowledge_base TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    knowledge_base TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    route TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    knowledge_base TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    payload_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    max_retries INTEGER NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT NOT NULL,
                    document_id TEXT,
                    chunk_count INTEGER NOT NULL,
                    queue_backend TEXT NOT NULL DEFAULT 'sqlite',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    trace_id TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    dead_lettered INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS roles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS permissions (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, role_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (role_id) REFERENCES roles(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS role_permissions (
                    role_id TEXT NOT NULL,
                    permission_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (role_id, permission_id),
                    FOREIGN KEY (role_id) REFERENCES roles(id),
                    FOREIGN KEY (permission_id) REFERENCES permissions(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_used_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_kb ON document_chunks(knowledge_base)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, id)"
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_due
                ON ingestion_jobs(status, next_attempt_at, created_at)
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id, created_at)"
            )
        self._ensure_ingestion_columns()
        self._ensure_post_migration_indexes()
        self._seed_access_control_data()

    def _ensure_ingestion_columns(self) -> None:
        self._ensure_column(
            table_name="ingestion_jobs",
            column_name="queue_backend",
            definition="TEXT NOT NULL DEFAULT 'sqlite'",
        )
        self._ensure_column(
            table_name="ingestion_jobs",
            column_name="idempotency_key",
            definition="TEXT NOT NULL DEFAULT ''",
        )
        self._ensure_column(
            table_name="ingestion_jobs",
            column_name="trace_id",
            definition="TEXT NOT NULL DEFAULT ''",
        )
        self._ensure_column(
            table_name="ingestion_jobs",
            column_name="attempt_count",
            definition="INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            table_name="ingestion_jobs",
            column_name="started_at",
            definition="TEXT",
        )
        self._ensure_column(
            table_name="ingestion_jobs",
            column_name="finished_at",
            definition="TEXT",
        )
        self._ensure_column(
            table_name="ingestion_jobs",
            column_name="dead_lettered",
            definition="INTEGER NOT NULL DEFAULT 0",
        )

    def _ensure_column(self, *, table_name: str, column_name: str, definition: str) -> None:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            rows = cursor.fetchall()
            existing = {str(row["name"]) for row in rows}
            if column_name in existing:
                return
            cursor.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )

    def _ensure_post_migration_indexes(self) -> None:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_idempotency
                ON ingestion_jobs(knowledge_base, idempotency_key)
                """
            )

    def _seed_access_control_data(self) -> None:
        permissions = (
            "chat:write",
            "ingestion:write",
            "ingestion:read",
            "ingestion:process",
            "metrics:read",
            "sessions:read",
            "sessions:write",
            "audit:read",
            "auth:manage_api_keys",
            "admin:full",
        )
        roles = (
            "admin",
            "operator",
            "viewer",
        )
        role_permission_map = {
            "admin": permissions,
            "operator": (
                "chat:write",
                "ingestion:write",
                "ingestion:read",
                "ingestion:process",
                "metrics:read",
                "sessions:read",
                "sessions:write",
            ),
            "viewer": (
                "ingestion:read",
                "metrics:read",
                "sessions:read",
            ),
        }
        with self.connection() as connection:
            cursor = connection.cursor()
            for code in permissions:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO permissions (id, code, created_at)
                    VALUES (lower(hex(randomblob(16))), ?, datetime('now'))
                    """,
                    (code,),
                )
            for role_name in roles:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO roles (id, name, created_at)
                    VALUES (lower(hex(randomblob(16))), ?, datetime('now'))
                    """,
                    (role_name,),
                )
            for role_name, permission_codes in role_permission_map.items():
                cursor.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
                role_row = cursor.fetchone()
                if role_row is None:
                    continue
                role_id = str(role_row["id"])
                for code in permission_codes:
                    cursor.execute("SELECT id FROM permissions WHERE code = ?", (code,))
                    permission_row = cursor.fetchone()
                    if permission_row is None:
                        continue
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO role_permissions (role_id, permission_id, created_at)
                        VALUES (?, ?, datetime('now'))
                        """,
                        (role_id, str(permission_row["id"])),
                    )
