"""Core type definitions for the agent framework."""

from dataclasses import dataclass, field
from typing import Any, TypedDict


class ToolUseBlock(TypedDict):
    """Represents a tool call from the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Usage:
    """Token usage tracking."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class QueryConfig:
    """Configuration for a query."""

    max_turns: int = 100
    max_budget_usd: float | None = None
    max_output_tokens: int | None = None
    temperature: float = 1.0
    system_prompt: str = ""


@dataclass
class ToolCall:
    """A pending tool call to be executed."""

    id: str
    name: str
    input: dict[str, Any]
