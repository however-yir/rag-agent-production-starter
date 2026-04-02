"""Vector-store abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.models import RetrievedDocument
from app.core.settings import AppSettings
from app.rag.embeddings import Embedder, get_embedder
from app.storage.vector_repository import VectorRepository


DEFAULT_POLICY_DOCUMENTS: tuple[RetrievedDocument, ...] = (
    RetrievedDocument(
        identifier="policy-001",
        title="Service Animal Conduct Policy",
        source="mock-policy",
        score=0.95,
        content=(
            "If a service animal becomes disruptive, staff should politely ask the "
            "handler to regain control. If the disruption continues, staff may ask "
            "the handler to remove the animal while still offering service access."
        ),
    ),
    RetrievedDocument(
        identifier="policy-002",
        title="Weather Escalation Procedure",
        source="mock-policy",
        score=0.41,
        content=(
            "Weather questions should be routed to live search when accurate current "
            "conditions are required."
        ),
    ),
)


class VectorStore(Protocol):
    def similarity_search(self, query: str, top_k: int) -> list[RetrievedDocument]:
        """Return the most relevant internal documents for a query."""


@dataclass(slots=True)
class StaticPolicyVectorStore:
    documents: tuple[RetrievedDocument, ...] = DEFAULT_POLICY_DOCUMENTS

    def similarity_search(self, query: str, top_k: int) -> list[RetrievedDocument]:
        query_terms = set(query.lower().split())
        ranked = []
        for document in self.documents:
            overlap = len(query_terms & set(document.content.lower().split()))
            ranked.append((overlap, document))
        ranked.sort(key=lambda item: (item[0], item[1].score), reverse=True)
        return [item[1] for item in ranked[:top_k]]


class SqlitePolicyVectorStore:
    """Persistent vector store backed by SQLite chunks."""

    def __init__(
        self,
        settings: AppSettings,
        repository: VectorRepository,
        embedder: Embedder | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.embedder = embedder or get_embedder(settings)

    def similarity_search(
        self,
        query: str,
        top_k: int,
        knowledge_base: str = "default",
    ) -> list[RetrievedDocument]:
        query_embedding = self.embedder.embed([query])[0]
        return self.repository.similarity_search(
            knowledge_base=knowledge_base,
            query_embedding=query_embedding,
            top_k=top_k,
        )


class PineconePolicyVectorStore:
    """Live Pinecone-backed policy lookup with SQLite/static fallback."""

    def __init__(
        self,
        settings: AppSettings,
        sqlite_store: SqlitePolicyVectorStore | None = None,
    ) -> None:
        self.settings = settings
        self.sqlite_store = sqlite_store
        self.mock_store = StaticPolicyVectorStore()

    def similarity_search(
        self,
        query: str,
        top_k: int,
        knowledge_base: str = "default",
    ) -> list[RetrievedDocument]:
        if self.sqlite_store is not None:
            sqlite_hits = self.sqlite_store.similarity_search(
                query=query,
                top_k=top_k,
                knowledge_base=knowledge_base,
            )
            if sqlite_hits:
                return sqlite_hits

        if self.settings.use_mock_services or not self.settings.live_vector_store_ready:
            return self.mock_store.similarity_search(query=query, top_k=top_k)

        from langchain_openai import OpenAIEmbeddings
        from langchain_pinecone import PineconeVectorStore

        embeddings = OpenAIEmbeddings(
            api_key=self.settings.openai_api_key,
            model=self.settings.embedding_model,
        )
        vector_store = PineconeVectorStore.from_existing_index(
            index_name=self.settings.pinecone_index_name,
            embedding=embeddings,
        )
        results = vector_store.similarity_search(query, k=top_k)
        documents = []
        for index, result in enumerate(results, start=1):
            documents.append(
                RetrievedDocument(
                    identifier=str(result.metadata.get("id", index)),
                    title=str(result.metadata.get("title", f"policy-{index}")),
                    content=result.page_content,
                    source=str(result.metadata.get("source", "pinecone")),
                    score=float(result.metadata.get("score", 0.0)),
                )
            )
        if documents:
            return documents
        return self.mock_store.similarity_search(query=query, top_k=top_k)
