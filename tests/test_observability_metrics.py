from __future__ import annotations

import unittest

from app.observability.metrics import MetricsRegistry, normalize_http_path


class ObservabilityMetricsTestCase(unittest.TestCase):
    def test_http_and_ingestion_metrics_are_recorded(self) -> None:
        metrics = MetricsRegistry()
        metrics.record_http_request(
            method="GET",
            path="/ingestion/jobs/123e4567-e89b-12d3-a456-426614174000",
            status_code=200,
            latency_ms=42.5,
        )
        metrics.record_ingestion_job(status="succeeded", backend="sqlite")
        snapshot = metrics.snapshot()

        self.assertIn("http", snapshot)
        self.assertIn("ingestion_jobs", snapshot)
        self.assertGreaterEqual(
            snapshot["http"]["requests"].get("GET /ingestion/jobs/{id} 200", 0),
            1,
        )
        self.assertEqual(
            snapshot["ingestion_jobs"]["status_counts"].get("succeeded", 0),
            1,
        )

    def test_normalize_http_path_replaces_dynamic_segments(self) -> None:
        self.assertEqual(
            normalize_http_path("/sessions/123/messages"),
            "/sessions/{id}/messages",
        )
        self.assertEqual(
            normalize_http_path("/ingestion/jobs/123e4567-e89b-12d3-a456-426614174000"),
            "/ingestion/jobs/{id}",
        )


if __name__ == "__main__":
    unittest.main()
