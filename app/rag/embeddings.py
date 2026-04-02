"""Embedding providers with a deterministic local fallback."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from app.core.settings import AppSettings


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        return values
    return [value / norm for value in values]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Convert a list of text chunks into vectors."""


class LocalHashEmbedder:
    """Deterministic embedder suitable for tests and offline development."""

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in text.lower().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = -1.0 if digest[4] % 2 else 1.0
                vector[index] += sign
            vectors.append(_normalize(vector))
        return vectors


class OpenAIEmbedder:
    """OpenAI embedding provider."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        from langchain_openai import OpenAIEmbeddings

        embeddings = OpenAIEmbeddings(
            api_key=self.settings.openai_api_key,
            model=self.settings.embedding_model,
        )
        return embeddings.embed_documents(texts)


def get_embedder(settings: AppSettings) -> Embedder:
    if settings.use_mock_services or not settings.live_llm_ready:
        return LocalHashEmbedder(settings.embedding_dimension)
    return OpenAIEmbedder(settings)

