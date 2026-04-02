"""Tavily web-search integration."""

from __future__ import annotations

import time

from app.core.logging import get_logger
from app.core.models import ToolInvocation
from app.core.request_context import get_request_context
from app.core.settings import AppSettings
from app.observability.metrics import get_metrics_registry
from app.observability.telemetry import start_span

logger = get_logger(__name__)


class TavilySearchClient:
    """A small wrapper that can run in live mode or deterministic mock mode."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def search(self, query: str) -> ToolInvocation:
        started = time.perf_counter()
        with start_span(
            "tool.tavily_search",
            kind="client",
            attributes={
                "tool.name": "tavily_search",
                "tool.query_length": len(query),
            },
        ):
            if self.settings.use_mock_services or not self.settings.live_search_ready:
                result = ToolInvocation(
                    name="tavily_search",
                    query=query,
                    output=(
                        "Mock Tavily result: weather and live-search queries should be "
                        "handled by the external web-search tool."
                    ),
                )
                _record_search_metrics(latency_ms=(time.perf_counter() - started) * 1000.0)
                return result

            import requests

            response = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.settings.tavily_api_key,
                    "query": query,
                    "max_results": self.settings.tavily_max_results,
                    "include_answer": True,
                },
                timeout=30,
            )
            response.raise_for_status()
            result = ToolInvocation(
                name="tavily_search",
                query=query,
                output=response.text,
            )
            _record_search_metrics(latency_ms=(time.perf_counter() - started) * 1000.0)
            return result

    def as_langchain_tool(self):
        try:
            from langchain_core.tools import tool
        except ImportError:  # pragma: no cover - optional dependency
            from langchain_community.tools import tool

        client = self

        @tool("tavily_search")
        def tavily_search(query: str) -> str:
            """Search the web for timely or general information."""

            return client.search(query).output

        return tavily_search


def _record_search_metrics(*, latency_ms: float) -> None:
    metrics = get_metrics_registry()
    metrics.record_latency("tool_tavily_search", latency_ms)
    context = get_request_context()
    logger.info(
        "TOOL_TAVILY | latency_ms=%.2f | trace_id=%s",
        latency_ms,
        context.trace_id,
    )
