from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api import dependencies as api_dependencies
from app.main import create_app


def _clear_dependency_caches() -> None:
    api_dependencies.get_ingestion_service_singleton.cache_clear()
    api_dependencies.get_rate_limiter.cache_clear()
    api_dependencies.get_auth_service.cache_clear()
    api_dependencies.get_audit_service.cache_clear()
    api_dependencies.get_auth_repository.cache_clear()
    api_dependencies.get_audit_repository.cache_clear()
    api_dependencies.get_ingestion_job_repository.cache_clear()
    api_dependencies.get_session_repository.cache_clear()
    api_dependencies.get_vector_repository.cache_clear()
    api_dependencies.get_database.cache_clear()
    api_dependencies.get_settings.cache_clear()


class SecurityBaselineTestCase(unittest.TestCase):
    def test_auth_rbac_rate_limit_and_audit_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = str(Path(tmp_dir) / "security.db")
            previous_env = {
                "DATABASE_PATH": os.environ.get("DATABASE_PATH"),
                "SECURITY_ENABLED": os.environ.get("SECURITY_ENABLED"),
                "BOOTSTRAP_ADMIN_USERNAME": os.environ.get("BOOTSTRAP_ADMIN_USERNAME"),
                "BOOTSTRAP_ADMIN_PASSWORD": os.environ.get("BOOTSTRAP_ADMIN_PASSWORD"),
                "USE_MOCK_SERVICES": os.environ.get("USE_MOCK_SERVICES"),
                "RATE_LIMIT_PER_MINUTE": os.environ.get("RATE_LIMIT_PER_MINUTE"),
            }
            os.environ["DATABASE_PATH"] = database_path
            os.environ["SECURITY_ENABLED"] = "true"
            os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
            os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
            os.environ["USE_MOCK_SERVICES"] = "true"
            os.environ["RATE_LIMIT_PER_MINUTE"] = "200"

            try:
                _clear_dependency_caches()
                client = TestClient(create_app())

                unauthorized = client.post(
                    "/chat",
                    json={"query": "hello", "mode": "mock", "knowledge_base": "default"},
                )
                self.assertEqual(unauthorized.status_code, 401)

                login = client.post(
                    "/auth/login",
                    json={"username": "admin", "password": "admin123"},
                )
                self.assertEqual(login.status_code, 200)
                token = login.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                chat = client.post(
                    "/chat",
                    json={
                        "query": "How should staff handle a disruptive service animal?",
                        "mode": "mock",
                        "knowledge_base": "default",
                    },
                    headers=headers,
                )
                self.assertEqual(chat.status_code, 200)

                first_enqueue = client.post(
                    "/ingestion/text",
                    json={
                        "knowledge_base": "default",
                        "source_name": "idempotency.txt",
                        "text": "Service disruptions should be handled with de-escalation.",
                    },
                    headers={**headers, "Idempotency-Key": "same-key-001"},
                )
                self.assertEqual(first_enqueue.status_code, 202)
                first_job_id = first_enqueue.json()["id"]

                second_enqueue = client.post(
                    "/ingestion/text",
                    json={
                        "knowledge_base": "default",
                        "source_name": "idempotency.txt",
                        "text": "Service disruptions should be handled with de-escalation.",
                    },
                    headers={**headers, "Idempotency-Key": "same-key-001"},
                )
                self.assertEqual(second_enqueue.status_code, 202)
                self.assertEqual(second_enqueue.json()["id"], first_job_id)

                audit = client.get("/admin/audit-logs", headers=headers)
                self.assertEqual(audit.status_code, 200)
                self.assertGreaterEqual(len(audit.json()), 1)
            finally:
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                _clear_dependency_caches()


if __name__ == "__main__":
    unittest.main()
