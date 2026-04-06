"""QueryEngine - Core agent loop using LangGraph.

Based on Claude Code's QueryEngine.ts pattern:
- Manages conversation state
- Handles tool execution loop
- Supports streaming responses
- Tracks usage and budget
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from ..llm.base import LLMClient, MessageParam, ToolUse
from ..tools.base import Tool, ToolResult, ToolUseContext
from ..tools.registry import ToolRegistry
from ..tools.executor import ToolExecutor
from .message import Message
from .types import Usage


# LangGraph State
class AgentState(TypedDict):
    """State for the agent graph."""

    messages: list[Message]
    usage: Usage
    pending_tool_results: list[ToolResult]
    should_continue: bool
    tool_results: dict[str, ToolResult]


@dataclass
class QueryEngineConfig:
    """Configuration for the query engine."""

    llm_client: LLMClient
    tool_registry: ToolRegistry
    system_prompt: str = ""
    max_turns: int = 100
    max_budget_usd: float | None = None
    model: str | None = None
    temperature: float = 1.0


class QueryEngine:
    """Main agent query engine.

    Uses LangGraph for state management and tool execution loop.

    Example:
        ```python
        config = QueryEngineConfig(
            llm_client=client,
            tool_registry=registry,
            system_prompt="You are a helpful assistant.",
        )
        engine = QueryEngine(config)

        async for event in engine.stream("Hello"):
            if isinstance(event, str):
                print(event, end="")
            elif isinstance(event, ToolResult):
                print(f"\n[Tool: {event.data}]\n")
        ```
    """

    def __init__(self, config: QueryEngineConfig) -> None:
        self.config = config
        self.abort_event = asyncio.Event()
        self._graph = self._build_graph()
        self._checkpointer = MemorySaver()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state graph."""
        graph = StateGraph(AgentState)

        # Add nodes
        graph.add_node("llm", self._llm_node)
        graph.add_node("tools", self._tools_node)

        # Add edges
        graph.add_edge("llm", "tools")
        graph.add_conditional_edges(
            "tools",
            self._should_continue,
            {
                "continue": "llm",
                "end": END,
            },
        )

        graph.set_entry_point("llm")

        return graph

    async def _llm_node(self, state: AgentState) -> dict:
        """Call the LLM with current messages."""
        messages = state["messages"]
        tools = self.config.tool_registry.list_tools()

        # Convert messages to LLM format
        llm_messages = []
        for m in messages:
            msg = MessageParam(role=m.role, content=m.content)
            # Add tool_calls for assistant messages
            if m.tool_calls:
                msg.tool_calls = m.tool_calls
            # Add tool_call_id for tool result messages
            if m.tool_call_id:
                msg.tool_call_id = m.tool_call_id
            llm_messages.append(msg)

        # Collect tool uses and text
        tool_uses: list[ToolUse] = []
        text_parts: list[str] = []

        async for event in self.config.llm_client.stream(
            llm_messages,
            tools=tools if tools else None,
            system_prompt=self.config.system_prompt,
            model=self.config.model,
            temperature=self.config.temperature,
        ):
            if isinstance(event, ToolUse):
                tool_uses.append(event)
            elif hasattr(event, "content"):
                text_parts.append(event.content)

        # Add assistant message
        if text_parts or tool_uses:
            assistant_content = "".join(text_parts)
            assistant_msg = Message.assistant(
                content=assistant_content or "[Using tools...]",
                tool_calls=[{
                    "id": t.id,
                    "name": t.name,
                    "input": t.input,
                } for t in tool_uses] if tool_uses else None,
            )
            state["messages"].append(assistant_msg)

        return {
            "messages": state["messages"],
            "pending_tool_results": [],
        }

    async def _tools_node(self, state: AgentState) -> dict:
        """Execute tools and return results."""
        messages = state["messages"]
        last_msg = messages[-1] if messages else None

        if not last_msg or not last_msg.tool_calls:
            return {"should_continue": False}

        # Execute tools
        context = ToolUseContext(
            abort_event=self.abort_event,
            tools=self.config.tool_registry.get_tools_dict(),
        )
        executor = ToolExecutor(context)

        from ..core.types import ToolUseBlock
        blocks: list[ToolUseBlock] = [
            {
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["input"],
            }
            for tc in (last_msg.tool_calls or [])
        ]

        results = await executor.execute_batch(blocks)

        # Add tool result messages
        tool_results: dict[str, ToolResult] = {}
        for block, result in zip(blocks, results):
            is_ok = result.is_success()
            content = result.result.data if is_ok else f"Error: {result.result.error}"
            tool_msg = Message.tool_result(
                tool_call_id=block["id"],
                tool_name=block["name"],
                content=content,
                is_error=not is_ok,
            )
            messages.append(tool_msg)
            tool_results[block["id"]] = result.result

        return {
            "messages": messages,
            "tool_results": tool_results,
            "should_continue": True,
        }

    def _should_continue(self, state: AgentState) -> str:
        """Determine if we should continue the loop."""
        if state.get("should_continue", False) is False:
            return "end"

        # Check max turns
        turns = len([m for m in state["messages"] if m.type in ("user", "assistant")])
        if turns >= self.config.max_turns:
            return "end"

        # Check abort
        if self.abort_event.is_set():
            return "end"

        return "continue"

    async def stream(
        self,
        user_input: str,
    ) -> AsyncGenerator[str | ToolResult | Message, None]:
        """Stream the agent response.

        Args:
            user_input: The user's message.

        Yields:
            Text deltas, tool results, and final messages.
        """
        # Add user message
        user_msg = Message.user(content=user_input)

        # Initial state
        initial_state: AgentState = {
            "messages": [user_msg],
            "usage": Usage(),
            "pending_tool_results": [],
            "should_continue": True,
            "tool_results": {},
        }

        # Run graph
        app = self._graph.compile(checkpointer=self._checkpointer)

        config = {"configurable": {"thread_id": "default"}}

        async for chunk in app.astream(initial_state, config):
            for node, output in chunk.items():
                if node == "llm":
                    # Yield text deltas
                    for msg in output.get("messages", []):
                        if msg.type == "assistant" and msg.content:
                            yield msg.content
                elif node == "tools":
                    # Yield tool results
                    for tool_id, result in output.get("tool_results", {}).items():
                        yield result

    def interrupt(self) -> None:
        """Interrupt the agent loop."""
        self.abort_event.set()

    def reset(self) -> None:
        """Reset the agent state."""
        self.abort_event.clear()
