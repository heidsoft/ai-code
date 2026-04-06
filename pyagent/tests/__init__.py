"""Tests for PyAgent."""

import pytest
from pyagent.tools.base import Tool, ToolResult, ToolUseContext
from pyagent.tools.registry import ToolRegistry
import asyncio


class MockTool(Tool[dict, str]):
    """A mock tool for testing."""

    name = "mock_tool"
    description = "A mock tool for testing"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string"},
            },
            "required": ["input"],
        }

    async def call(
        self,
        args: dict,
        context: ToolUseContext,
    ) -> ToolResult[str]:
        return ToolResult(data=f"Processed: {args.get('input')}")

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tool_registry():
    """Create a tool registry with a mock tool."""
    registry = ToolRegistry()
    registry.register(MockTool())
    return registry


@pytest.fixture
def tool_context():
    """Create a tool context."""
    return ToolUseContext(
        abort_event=asyncio.Event(),
        tools={},
    )


def test_tool_registry_register(tool_registry):
    """Test tool registration."""
    assert "mock_tool" in tool_registry
    assert tool_registry.get("mock_tool") is not None
    assert tool_registry.get("nonexistent") is None


def test_tool_registry_list_tools(tool_registry):
    """Test listing tools."""
    tools = tool_registry.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "mock_tool"


def test_tool_registry_len(tool_registry):
    """Test registry length."""
    assert len(tool_registry) == 1


@pytest.mark.asyncio
async def test_mock_tool_call():
    """Test mock tool execution."""
    tool = MockTool()
    context = ToolUseContext(
        abort_event=asyncio.Event(),
        tools={},
    )

    result = await tool.call({"input": "test"}, context)

    assert result.is_success()
    assert result.data == "Processed: test"


@pytest.mark.asyncio
async def test_tool_context_abort():
    """Test abort signal."""
    event = asyncio.Event()
    context = ToolUseContext(abort_event=event)

    assert not context.is_aborted()

    event.set()
    assert context.is_aborted()


def test_tool_definition_matches_name():
    """Test tool name matching."""
    registry = ToolRegistry()
    tool = MockTool()
    registry.register(tool, aliases=["alias1", "alias2"])

    assert registry.get("mock_tool") is not None
    assert registry.get("alias1") is not None
    assert registry.get("alias2") is not None
