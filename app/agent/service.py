"""ReAct agent orchestration."""

from __future__ import annotations

import time

from app.core.logging import get_logger
from app.core.models import AgentResponse, ToolInvocation
from app.core.request_context import get_request_context
from app.core.settings import AppSettings
from app.observability.metrics import get_metrics_registry
from app.observability.telemetry import start_span
from app.rag.service import PolicySearchService
from app.tools.tavily import TavilySearchClient

logger = get_logger(__name__)


class ReActAgentService:
    """High-level facade for demo execution, API usage, and future workflow growth."""

    def __init__(
        self,
        settings: AppSettings,
        tavily_client: TavilySearchClient | None = None,
        policy_service: PolicySearchService | None = None,
    ) -> None:
        self.settings = settings
        self.tavily_client = tavily_client or TavilySearchClient(settings)
        self.policy_service = policy_service or PolicySearchService(settings)

    def answer(
        self,
        query: str,
        *,
        knowledge_base: str = "default",
        session_history: list[dict[str, object]] | None = None,
    ) -> AgentResponse:
        started = time.perf_counter()
        history = session_history or []
        with start_span(
            "agent.answer",
            attributes={
                "agent.query_length": len(query),
                "agent.knowledge_base": knowledge_base,
                "agent.history_items": len(history),
                "agent.mode": "mock" if self.settings.use_mock_services else "live",
            },
        ):
            if self.settings.enable_langgraph_agent and not self.settings.use_mock_services:
                try:
                    response = self._answer_with_langgraph(query, history=history)
                    self._record_metrics(
                        response=response,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    return response
                except Exception:
                    pass
            response = self._answer_with_fallback(
                query=query,
                knowledge_base=knowledge_base,
                history=history,
            )
            self._record_metrics(
                response=response,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
            return response

    def _answer_with_langgraph(
        self,
        query: str,
        *,
        history: list[dict[str, object]],
    ) -> AgentResponse:
        from langchain.chat_models import init_chat_model
        from langchain_core.prompts import ChatPromptTemplate
        from langgraph.prebuilt import create_react_agent

        history_text = _history_to_text(history, max_items=8)
        model = init_chat_model(
            model=self.settings.openai_model,
            model_provider="openai",
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.settings.system_prompt),
                (
                    "human",
                    "Conversation history:\n{history}\n\nCurrent question:\n{text}",
                ),
            ]
        )
        tools = [
            self.tavily_client.as_langchain_tool(),
            self.policy_service.as_langchain_tool(),
        ]
        agent_executor = create_react_agent(model, tools)
        response = agent_executor.invoke(
            prompt.invoke({"text": query, "history": history_text})
        )
        messages = response.get("messages", [])
        last_message = messages[-1] if messages else None
        answer = getattr(last_message, "content", str(last_message))
        return AgentResponse(
            answer=str(answer),
            route="react_agent_langgraph",
            metadata={
                "message_count": len(messages),
                "history_count": len(history),
            },
        )

    def _answer_with_fallback(
        self,
        *,
        query: str,
        knowledge_base: str,
        history: list[dict[str, object]],
    ) -> AgentResponse:
        lowered = query.lower()
        if any(token in lowered for token in ("weather", "temperature", "today", "current")):
            tool_call = self.tavily_client.search(query)
            return AgentResponse(
                answer=(
                    "Fallback agent chose Tavily search because the query depends on "
                    "live external information."
                ),
                route="tavily_search",
                tool_calls=[tool_call],
                metadata={
                    "mode": "heuristic-fallback",
                    "history_count": len(history),
                },
            )

        policy_result = self.policy_service.answer_query(
            query=query,
            knowledge_base=knowledge_base,
        )
        policy_result.metadata["mode"] = "heuristic-fallback"
        policy_result.metadata["history_count"] = len(history)
        return policy_result

    def _record_metrics(self, *, response: AgentResponse, latency_ms: float) -> None:
        metrics = get_metrics_registry()
        metrics.record_route(response.route)
        metrics.record_latency("chat_total", latency_ms)
        context = get_request_context()
        response.metadata["latency_ms"] = round(latency_ms, 4)
        response.metadata.setdefault("trace_id", context.trace_id)
        response.metadata.setdefault("request_id", context.request_id)
        if context.session_id:
            response.metadata.setdefault("session_id", context.session_id)
        logger.info(
            "CHAT_RESULT | route=%s | latency_ms=%.2f | evidence=%s",
            response.route,
            latency_ms,
            len(response.evidence),
        )


def _history_to_text(history: list[dict[str, object]], max_items: int = 8) -> str:
    if not history:
        return "No previous messages."
    selected = history[-max_items:]
    lines = []
    for item in selected:
        role = str(item.get("role", "unknown"))
        content = str(item.get("content", ""))
        lines.append(f"{role}: {content}")
    return "\n".join(lines)
