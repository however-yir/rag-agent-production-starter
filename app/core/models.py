"""Shared application models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RetrievedDocument:
    identifier: str
    title: str
    content: str
    source: str
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ToolInvocation:
    name: str
    query: str
    output: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentResponse:
    answer: str
    route: str
    evidence: list[RetrievedDocument] = field(default_factory=list)
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "route": self.route,
            "evidence": [item.to_dict() for item in self.evidence],
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "metadata": self.metadata,
        }
