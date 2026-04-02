from __future__ import annotations

import unittest

from app.core.models import RetrievedDocument
from app.core.settings import AppSettings
from app.rag.service import PolicySearchService
from app.rag.vector_store import StaticPolicyVectorStore


class PolicySearchServiceTestCase(unittest.TestCase):
    def test_policy_search_returns_evidence(self) -> None:
        settings = AppSettings(use_mock_services=True)
        store = StaticPolicyVectorStore(
            documents=(
                RetrievedDocument(
                    identifier="policy-1",
                    title="Service Animals",
                    content="Staff should ask the handler to regain control.",
                    source="test",
                    score=1.0,
                ),
            )
        )
        service = PolicySearchService(settings=settings, vector_store=store)

        result = service.answer_query("How should staff respond to a disruptive service animal?")

        self.assertEqual(result.route, "policy_search")
        self.assertEqual(len(result.evidence), 1)
        self.assertIn("regain control", result.answer)


if __name__ == "__main__":
    unittest.main()
