from __future__ import annotations

import unittest

from app.agent.service import ReActAgentService
from app.core.settings import AppSettings


class ReActAgentServiceTestCase(unittest.TestCase):
    def test_weather_queries_route_to_live_search_tool(self) -> None:
        settings = AppSettings(use_mock_services=True)
        result = ReActAgentService(settings).answer("What is the weather in Kanyakumari today?")
        self.assertEqual(result.route, "tavily_search")
        self.assertEqual(result.tool_calls[0].name, "tavily_search")

    def test_policy_queries_route_to_policy_search(self) -> None:
        settings = AppSettings(use_mock_services=True)
        result = ReActAgentService(settings).answer(
            "How should staff respond if a service animal becomes disruptive?"
        )
        self.assertEqual(result.route, "policy_search")
        self.assertGreaterEqual(len(result.evidence), 1)


if __name__ == "__main__":
    unittest.main()
