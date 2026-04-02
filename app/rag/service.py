"""RAG service layer."""

from __future__ import annotations

import time
from typing import TypedDict

from app.core.logging import get_logger
from app.core.models import AgentResponse, RetrievedDocument, ToolInvocation
from app.core.request_context import get_request_context
from app.core.settings import AppSettings
from app.observability.metrics import get_metrics_registry
from app.observability.telemetry import start_span
from app.rag.vector_store import PineconePolicyVectorStore, SqlitePolicyVectorStore, VectorStore
from app.storage.vector_repository import VectorRepository

logger = get_logger(__name__)


class PolicySearchService:
    """Encapsulates policy retrieval and answer generation."""

    def __init__(
        self,
        settings: AppSettings,
        vector_store: VectorStore | None = None,
        vector_repository: VectorRepository | None = None,
    ) -> None:
        self.settings = settings
        if vector_store is not None:
            self.vector_store = vector_store
        else:
            sqlite_store = None
            if vector_repository is not None:
                sqlite_store = SqlitePolicyVectorStore(
                    settings=settings,
                    repository=vector_repository,
                )
            self.vector_store = PineconePolicyVectorStore(
                settings=settings,
                sqlite_store=sqlite_store,
            )

    def answer_query(self, query: str, knowledge_base: str = "default") -> AgentResponse:
        started = time.perf_counter()
        metrics = get_metrics_registry()
        with start_span(
            "rag.answer_query",
            attributes={
                "rag.knowledge_base": knowledge_base,
                "rag.query_length": len(query),
                "rag.mode": "mock" if self.settings.use_mock_services else "live",
            },
        ):
            if self.settings.enable_langgraph_rag and not self.settings.use_mock_services:
                try:
                    response = self._answer_with_langgraph(query, knowledge_base=knowledge_base)
                    metrics.record_latency("rag_total", (time.perf_counter() - started) * 1000.0)
                    metrics.record_rag_hit(len(response.evidence))
                    return response
                except Exception:
                    # The heavyweight layout prefers graceful degradation over demo breakage.
                    pass

            documents = self._similarity_search(query=query, knowledge_base=knowledge_base)
            context = "\n\n".join(document.content for document in documents)
            answer = self._build_answer(query=query, context=context, documents=documents)
            response = AgentResponse(
                answer=answer,
                route="policy_search",
                evidence=documents,
                tool_calls=[
                    ToolInvocation(
                        name="policy_search",
                        query=query,
                        output=context or "No matching context was retrieved.",
                    )
                ],
                metadata={
                    "evidence_count": len(documents),
                    "knowledge_base": knowledge_base,
                },
            )
            context = get_request_context()
            response.metadata.setdefault("trace_id", context.trace_id)
            metrics.record_latency("rag_total", (time.perf_counter() - started) * 1000.0)
            metrics.record_rag_hit(len(documents))
            logger.info(
                "RAG_RESULT | kb=%s | query=%s | evidence=%s",
                knowledge_base,
                query,
                len(documents),
            )
            return response

    def _answer_with_langgraph(self, query: str, knowledge_base: str) -> AgentResponse:
        from langchain.chat_models import init_chat_model
        from langchain_core.prompts import ChatPromptTemplate
        from langgraph.graph import END, START, StateGraph

        service = self

        class RAGState(TypedDict):
            question: str
            context: str
            answer: str

        def retrieve(state: RAGState) -> RAGState:
            documents = service._similarity_search(
                query=state["question"],
                knowledge_base=knowledge_base,
            )
            state["context"] = "\n\n".join(document.content for document in documents)
            return state

        def generate(state: RAGState) -> RAGState:
            llm = init_chat_model(
                model=service.settings.openai_model,
                model_provider="openai",
            )
            prompt_template = ChatPromptTemplate.from_messages(
                [
                    ("system", service.settings.policy_system_prompt),
                    (
                        "human",
                        "Answer the question using only the context below.\n\n"
                        "Context:\n{context}\n\nQuestion:\n{question}",
                    ),
                ]
            )
            prompt = prompt_template.invoke(
                {"context": state["context"], "question": state["question"]}
            )
            state["answer"] = str(llm.invoke(prompt).content)
            return state

        graph = StateGraph(RAGState)
        graph.add_node("retrieve", retrieve)
        graph.add_node("generate", generate)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", END)

        result = graph.compile().invoke({"question": query, "context": "", "answer": ""})
        documents = self._similarity_search(query=query, knowledge_base=knowledge_base)
        context = result.get("context", "")
        return AgentResponse(
            answer=result["answer"],
            route="policy_search_langgraph",
            evidence=documents,
            tool_calls=[
                ToolInvocation(
                    name="policy_search",
                    query=query,
                    output=context or "No matching context was retrieved.",
                )
            ],
            metadata={
                "workflow": "langgraph",
                "evidence_count": len(documents),
                "knowledge_base": knowledge_base,
            },
        )

    def _similarity_search(
        self,
        *,
        query: str,
        knowledge_base: str,
    ) -> list[RetrievedDocument]:
        top_k = self.settings.retrieval_top_k
        try:
            return self.vector_store.similarity_search(
                query=query,
                top_k=top_k,
                knowledge_base=knowledge_base,
            )
        except TypeError:
            # Backward compatibility for simple vector store implementations.
            return self.vector_store.similarity_search(query=query, top_k=top_k)

    def _build_answer(
        self,
        query: str,
        context: str,
        documents: list[RetrievedDocument],
    ) -> str:
        if not documents:
            return "No internal policy context was found for this question."

        if self.settings.use_mock_services or not self.settings.live_llm_ready:
            return (
                f"Mock policy answer for '{query}'. The most relevant guidance is: "
                f"{documents[0].content}"
            )

        from langchain.chat_models import init_chat_model
        from langchain_core.prompts import ChatPromptTemplate

        llm = init_chat_model(
            model=self.settings.openai_model,
            model_provider="openai",
        )
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", self.settings.policy_system_prompt),
                (
                    "human",
                    "Answer the following based only on the given context.\n\n"
                    "Context:\n{context}\n\nQuestion:\n{question}",
                ),
            ]
        )
        prompt = prompt_template.invoke({"context": context, "question": query})
        return str(llm.invoke(prompt).content)

    def as_langchain_tool(self):
        try:
            from langchain_core.tools import tool
        except ImportError:  # pragma: no cover - optional dependency
            from langchain_community.tools import tool

        service = self

        @tool("policy_search")
        def policy_search(query: str) -> str:
            """Search the internal policy corpus for organization-specific questions."""

            return service.answer_query(query=query, knowledge_base="default").answer

        return policy_search
