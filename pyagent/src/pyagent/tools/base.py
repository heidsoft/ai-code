"""Tool interface definitions.

Based on the Claude Code harness engineering patterns:
- Tool is a generic interface with call(), input_schema, is_read_only, is_concurrency_safe
- ToolUseContext is the dependency injection container
- ToolResult wraps execution results
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

import asyncio


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass
class ToolResult:
    """Result of a tool execution.

    Attributes:
        data: The returned data from the tool.
        error: Error message if the tool failed.
        metadata: Additional metadata about the execution.
    """

    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_success(self) -> bool:
        return self.error is None


@dataclass
class ToolUseContext:
    """Dependency injection container for tool execution.

    Provides access to runtime services like abort signals,
    other tools, and arbitrary metadata.

    Attributes:
        abort_event: Event to check for cancellation.
        tools: Registry of available tools.
        metadata: Arbitrary runtime metadata.
    """

    abort_event: asyncio.Event
    tools: dict[str, Tool] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_aborted(self) -> bool:
        return self.abort_event.is_set()


class Tool(ABC, Generic[InputT, OutputT]):
    """Abstract base class for tools.

    A tool represents a callable capability that the agent can use.
    Tools have a name, description, input schema, and execution logic.

    Type parameters:
        InputT: The input type (usually dict or a TypedDict).
        OutputT: The output type.

    Example:
        ```python
        class MyTool(Tool[dict, str]):
            name = "my_tool"
            description = "Does something useful"

            def input_schema(self) -> dict:
                return {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}
                    },
                    "required": ["query"]
                }

            async def call(
                self,
                args: dict,
                context: ToolUseContext
            ) -> ToolResult[str]:
                query = args.get("query")
                result = await do_something(query)
                return ToolResult(data=result)
        ```
    """

    # Subclasses must set these
    name: str = ""
    description: str = ""

    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """Return the JSON Schema for the tool's input.

        Returns:
            A dict representing the JSON Schema of the input parameters.
        """

    @abstractmethod
    async def call(
        self,
        args: InputT,
        context: ToolUseContext,
    ) -> ToolResult[OutputT]:
        """Execute the tool with the given arguments.

        Args:
            args: The validated input arguments.
            context: The tool execution context.

        Returns:
            ToolResult containing the output data or error.
        """

    def is_read_only(self, args: InputT) -> bool:
        """Check if this tool performs only read operations.

        Override to return True for tools that don't modify state.
        This enables concurrent execution of read-only tools.

        Args:
            args: The input arguments.

        Returns:
            True if the tool is read-only, False otherwise.
        """
        return False

    def is_concurrency_safe(self, args: InputT) -> bool:
        """Check if this tool is safe to execute concurrently.

        Tools that are idempotent and don't have side effects
        can be executed in parallel with other tools.

        Args:
            args: The input arguments.

        Returns:
            True if concurrent execution is safe, False otherwise.
        """
        return False

    def is_destructive(self, args: InputT) -> bool:
        """Check if this tool performs destructive operations.

        Destructive tools (delete, overwrite, send) require
        extra confirmation from the user.

        Args:
            args: The input arguments.

        Returns:
            True if the tool is destructive.
        """
        return False

    def get_path(self, args: InputT) -> str | None:
        """Get the file path this tool operates on, if any.

        Args:
            args: The input arguments.

        Returns:
            The file path if applicable, None otherwise.
        """
        return None

    def get_description(
        self,
        args: InputT | None = None,
    ) -> str:
        """Get the description of this tool for the model.

        Override to provide dynamic descriptions based on args.

        Args:
            args: Optional input arguments for dynamic description.

        Returns:
            The tool description.
        """
        return self.description


@dataclass
class ToolDefinition:
    """Definition of a tool for registration and LLM exposure.

    This is the runtime representation that includes
    both the tool instance and its metadata.
    """

    tool: Tool
    aliases: list[str] = field(default_factory=list)
    is_deferred: bool = False
    always_load: bool = False
    max_result_size_chars: int = 100_000

    def matches_name(self, name: str) -> bool:
        """Check if this tool matches the given name or alias."""
        if self.tool.name == name:
            return True
        return name in self.aliases
