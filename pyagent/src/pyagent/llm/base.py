"""LLM client abstraction.

Based on Claude Code's API client pattern:
- Abstract base for multi-provider support
- Streaming response support
- Tool call handling
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Literal

from ..core.types import Usage


@dataclass
class MessageParam:
    """A message parameter for the LLM."""

    role: Literal["user", "assistant", "system"]
    content: str
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


@dataclass
class ToolUse:
    """A tool use from the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """A text response from the LLM."""

    content: str
    tool_uses: list[ToolUse] | None = None
    stop_reason: str | None = None
    usage: Usage | None = None


@dataclass
class StreamEvent:
    """A streaming event from the LLM."""

    type: Literal[
        "content_start",
        "content_delta",
        "content_end",
        "tool_use",
        "message_end",
        "error",
    ]
    data: Any = None


class LLMClient(ABC):
    """Abstract base class for LLM clients.

    Provides a unified interface for different LLM providers
    (Anthropic, OpenAI, etc.)
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[MessageParam],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a complete response (non-streaming).

        Args:
            messages: Conversation history.
            tools: Available tools for the LLM.
            system_prompt: System prompt to prepend.
            model: Model to use (provider-specific).
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.

        Returns:
            The complete LLM response.
        """

    @abstractmethod
    async def stream(
        self,
        messages: list[MessageParam],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMResponse | ToolUse, None]:
        """Generate a streaming response.

        Args:
            messages: Conversation history.
            tools: Available tools for the LLM.
            system_prompt: System prompt to prepend.
            model: Model to use (provider-specific).
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.

        Yields:
            Text deltas and tool uses as they arrive.
        """
