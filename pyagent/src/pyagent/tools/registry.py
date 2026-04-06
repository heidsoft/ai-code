"""Tool registry for managing available tools.

Based on Claude Code's tool registry pattern:
- Central registration for all tools
- Lookup by name or alias
- LLM-compatible tool list generation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Tool, ToolDefinition, ToolResult, ToolUseContext


class ToolRegistry:
    """Central registry for all available tools.

    Manages tool registration, lookup, and provides
    a LLM-compatible tool list.

    Example:
        ```python
        registry = ToolRegistry()
        registry.register(BashTool())
        registry.register(HttpTool())

        # Get tool by name
        tool = registry.get("bash")

        # Get all tools for LLM
        llm_tools = registry.list_tools()
        ```
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._aliases: dict[str, str] = {}  # alias -> canonical name

    def register(
        self,
        tool: Tool,
        *,
        aliases: list[str] | None = None,
        is_deferred: bool = False,
        always_load: bool = False,
        max_result_size_chars: int = 100_000,
    ) -> None:
        """Register a tool.

        Args:
            tool: The tool instance to register.
            aliases: Optional list of aliases for the tool.
            is_deferred: Whether the tool is deferred (loaded on demand).
            always_load: Whether to always load this tool.
            max_result_size_chars: Max result size before truncation.
        """
        definition = ToolDefinition(
            tool=tool,
            aliases=aliases or [],
            is_deferred=is_deferred,
            always_load=always_load,
            max_result_size_chars=max_result_size_chars,
        )
        self._tools[tool.name] = definition

        # Register aliases
        for alias in (aliases or []):
            self._aliases[alias] = tool.name

    def get(self, name: str) -> Tool | None:
        """Get a tool by name or alias.

        Args:
            name: The tool name or alias.

        Returns:
            The tool if found, None otherwise.
        """
        # Check aliases first
        if name in self._aliases:
            name = self._aliases[name]

        definition = self._tools.get(name)
        return definition.tool if definition else None

    def get_definition(self, name: str) -> ToolDefinition | None:
        """Get a tool definition by name or alias.

        Args:
            name: The tool name or alias.

        Returns:
            The tool definition if found, None otherwise.
        """
        if name in self._aliases:
            name = self._aliases[name]
        return self._tools.get(name)

    def list_tools(
        self,
        include_deferred: bool = False,
    ) -> list[dict[str, Any]]:
        """Get a list of all tools in LLM-compatible format.

        Args:
            include_deferred: Whether to include deferred tools.

        Returns:
            List of tool definitions for the LLM.
        """
        tools = []
        for defn in self._tools.values():
            if defn.is_deferred and not include_deferred:
                continue
            if defn.always_load or not defn.is_deferred:
                tools.append({
                    "name": defn.tool.name,
                    "description": defn.tool.description,
                    "input_schema": defn.tool.input_schema(),
                })
        return tools

    def get_tools_dict(self) -> dict[str, Tool]:
        """Get a dict of name -> Tool for context creation.

        Returns:
            Dict mapping tool names to tool instances.
        """
        return {name: defn.tool for name, defn in self._tools.items()}

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None
