"""Message type definitions for the agent conversation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from datetime import datetime


@dataclass
class Message:
    """Unified message type for the conversation.

    This is a simple dataclass-based message type without inheritance
    to avoid issues with field ordering in dataclasses.
    """

    type: Literal["user", "assistant", "system", "tool", "compact_boundary"]
    role: str
    content: Any
    timestamp: datetime = field(default_factory=datetime.now)
    name: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] | None = None
    stop_reason: str | None = None

    @classmethod
    def user(cls, content: str) -> "Message":
        """Create a user message."""
        return cls(type="user", role="user", content=content)

    @classmethod
    def assistant(
        cls,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        stop_reason: str | None = None,
    ) -> "Message":
        """Create an assistant message."""
        return cls(
            type="assistant",
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )

    @classmethod
    def system(cls, content: str) -> "Message":
        """Create a system message."""
        return cls(type="system", role="system", content=content)

    @classmethod
    def tool_result(
        cls,
        tool_call_id: str,
        tool_name: str,
        content: str,
        is_error: bool = False,
    ) -> "Message":
        """Create a tool result message."""
        return cls(
            type="tool",
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            is_error=is_error,
        )


# Backwards compatibility aliases
UserMessage = Message
AssistantMessage = Message
SystemMessage = Message
ToolResultMessage = Message
