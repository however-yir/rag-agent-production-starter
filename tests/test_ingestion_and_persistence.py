from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from app.agent.service import ReActAgentService
from app.core.settings import AppSettings
from app.ingestion.service import IngestionService
from app.observability.metrics import get_metrics_registry
from app.rag.service import PolicySearchService
from app.storage.database import Database
from app.storage.ingestion_job_repository import IngestionJobRepository
from app.storage.session_repository import SessionRepository
from app.storage.vector_repository import VectorRepository


def _build_settings(db_path: str) -> AppSettings:
    return AppSettings(
        use_mock_services=True,
        database_path=db_path,
        chunk_size=120,
        chunk_overlap=20,
        retrieval_top_k=3,
        ingestion_retry_backoff_seconds=0,
        ingestion_retry_max_backoff_seconds=0,
    )


class IngestionPersistenceAndEvalTestCase(unittest.TestCase):
    def test_ingestion_pipeline_supports_rag_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = _build_settings(str(Path(tmp_dir) / "test.db"))
            database = Database(settings)
            vector_repository = VectorRepository(database)
            ingestion_service = IngestionService(settings, vector_repository)
            rag_service = PolicySearchService(settings, vector_repository=vector_repository)

            ingestion = ingestion_service.ingest_text(
                knowledge_base="hr",
                source_name="employee_handbook.txt",
                text=(
                    "Service animal incidents must be de-escalated by asking the handler "
                    "to regain control before further action."
                ),
            )
            self.assertGreaterEqual(ingestion.chunk_count, 1)

            response = rag_service.answer_query(
                "How should staff handle a disruptive service animal?",
                knowledge_base="hr",
            )
            self.assertGreaterEqual(len(response.evidence), 1)
            self.assertEqual(response.metadata["knowledge_base"], "hr")

    def test_session_history_is_persisted_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = _build_settings(str(Path(tmp_dir) / "test.db"))
            repository = SessionRepository(Database(settings))
            session_id = repository.create_session()
            repository.add_message(session_id=session_id, role="user", content="hello")
            repository.add_message(
                session_id=session_id,
                role="assistant",
                content="hi",
                route="policy_search",
                latency_ms=12.5,
            )

            messages = repository.get_messages(session_id=session_id)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[1]["route"], "policy_search")

    def test_regression_latency_and_route_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = _build_settings(str(Path(tmp_dir) / "test.db"))
            service = ReActAgentService(settings=settings)
            metrics = get_metrics_registry()
            before = metrics.snapshot()

            started = time.perf_counter()
            response = service.answer("What is the weather in Kanyakumari today?")
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            self.assertEqual(response.route, "tavily_search")
            self.assertLess(elapsed_ms, 500.0)
            self.assertLess(float(response.metadata["latency_ms"]), 500.0)

            after = metrics.snapshot()
            before_count = before["routes"].get("tavily_search", 0)
            after_count = after["routes"].get("tavily_search", 0)
            self.assertGreaterEqual(after_count, before_count + 1)

    def test_ingestion_queue_processes_enqueued_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = _build_settings(str(Path(tmp_dir) / "test.db"))
            database = Database(settings)
            vector_repository = VectorRepository(database)
            ingestion_service = IngestionService(
                settings=settings,
                repository=vector_repository,
                job_repository=IngestionJobRepository(database),
            )
            rag_service = PolicySearchService(settings, vector_repository=vector_repository)

            job = ingestion_service.enqueue_text(
                knowledge_base="hr",
                source_name="queue_doc.txt",
                text="Escalate unsafe disruptions and ask handlers to regain control.",
            )
            self.assertEqual(job.status, "queued")

            processed = ingestion_service.process_jobs(max_jobs=3)
            self.assertGreaterEqual(processed, 1)

            updated_job = ingestion_service.get_job(job.id)
            self.assertIsNotNone(updated_job)
            assert updated_job is not None
            self.assertEqual(updated_job.status, "succeeded")
            self.assertGreater(updated_job.chunk_count, 0)

            response = rag_service.answer_query(
                "What should staff do if service animals become disruptive?",
                knowledge_base="hr",
            )
            self.assertGreaterEqual(len(response.evidence), 1)

    def test_ingestion_queue_retries_and_marks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = _build_settings(str(Path(tmp_dir) / "test.db"))
            database = Database(settings)
            service = IngestionService(
                settings=settings,
                repository=VectorRepository(database),
                job_repository=IngestionJobRepository(database),
            )

            job = service.enqueue_text(
                knowledge_base="default",
                source_name="bad.txt",
                text="   ",
                max_retries=2,
            )
            for _ in range(6):
                if service.process_jobs(max_jobs=1) == 0:
                    break

            updated_job = service.get_job(job.id)
            self.assertIsNotNone(updated_job)
            assert updated_job is not None
            self.assertEqual(updated_job.status, "failed")
            self.assertGreater(updated_job.retry_count, updated_job.max_retries)
            self.assertIn("No text content", updated_job.last_error)


if __name__ == "__main__":
    unittest.main()
