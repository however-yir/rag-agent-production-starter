"""Repositories for documents and chunk vectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import uuid

from app.core.models import RetrievedDocument
from app.storage.database import Database


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    numerator = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return numerator / (norm_a * norm_b)


@dataclass(slots=True)
class IngestedDocument:
    id: str
    knowledge_base: str
    source_name: str
    source_type: str
    chunk_count: int


class VectorRepository:
    """Stores and retrieves embedded chunks from SQLite."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def add_document_with_chunks(
        self,
        *,
        knowledge_base: str,
        source_name: str,
        source_type: str,
        chunks: list[str],
        embeddings: list[list[float]],
        metadata: dict[str, str] | None = None,
    ) -> IngestedDocument:
        document_id = str(uuid.uuid4())
        metadata_json = json.dumps(metadata or {}, ensure_ascii=True)
        created_at = _utcnow()
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO documents (id, knowledge_base, source_name, source_type, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, knowledge_base, source_name, source_type, metadata_json, created_at),
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                cursor.execute(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, knowledge_base, chunk_index, content, embedding_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        document_id,
                        knowledge_base,
                        index,
                        chunk,
                        json.dumps(embedding),
                        created_at,
                    ),
                )
        return IngestedDocument(
            id=document_id,
            knowledge_base=knowledge_base,
            source_name=source_name,
            source_type=source_type,
            chunk_count=len(chunks),
        )

    def similarity_search(
        self,
        *,
        knowledge_base: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[RetrievedDocument]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT c.id, c.document_id, c.content, c.embedding_json, c.chunk_index, d.source_name, d.source_type
                FROM document_chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.knowledge_base = ?
                """,
                (knowledge_base,),
            )
            rows = cursor.fetchall()

        scored_rows: list[tuple[float, object]] = []
        for row in rows:
            chunk_embedding = json.loads(row["embedding_json"])
            score = _cosine_similarity(query_embedding, chunk_embedding)
            scored_rows.append((score, row))
        scored_rows.sort(key=lambda item: item[0], reverse=True)

        documents = []
        for score, row in scored_rows[:top_k]:
            documents.append(
                RetrievedDocument(
                    identifier=str(row["id"]),
                    title=str(row["source_name"]),
                    content=str(row["content"]),
                    source=str(row["source_type"]),
                    score=score,
                )
            )
        return documents

    def list_documents(self, knowledge_base: str | None = None) -> list[dict[str, object]]:
        with self.database.connection() as connection:
            cursor = connection.cursor()
            if knowledge_base:
                cursor.execute(
                    """
                    SELECT d.id, d.knowledge_base, d.source_name, d.source_type, d.created_at,
                           COUNT(c.id) AS chunk_count
                    FROM documents d
                    LEFT JOIN document_chunks c ON c.document_id = d.id
                    WHERE d.knowledge_base = ?
                    GROUP BY d.id
                    ORDER BY d.created_at DESC
                    """,
                    (knowledge_base,),
                )
            else:
                cursor.execute(
                    """
                    SELECT d.id, d.knowledge_base, d.source_name, d.source_type, d.created_at,
                           COUNT(c.id) AS chunk_count
                    FROM documents d
                    LEFT JOIN document_chunks c ON c.document_id = d.id
                    GROUP BY d.id
                    ORDER BY d.created_at DESC
                    """
                )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

