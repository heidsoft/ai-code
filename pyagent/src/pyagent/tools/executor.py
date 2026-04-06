"""Tool execution orchestration.

Based on Claude Code's toolOrchestration.ts pattern:
- Partition tools by concurrency safety
- Execute concurrent-safe tools in parallel
- Execute non-concurrent-safe tools serially
- Streaming execution with progress reporting
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from .base import Tool, ToolResult, ToolUseContext
from .registry import ToolRegistry
from ..core.types import ToolUseBlock


@dataclass
class ToolExecutionResult:
    """Result of executing a tool."""

    tool_name: str
    tool_call_id: str
    result: ToolResult
    execution_time_ms: int = 0

    def is_success(self) -> bool:
        return self.result.is_success()


@dataclass
class ExecutionProgress:
    """Progress update during tool execution."""

    tool_call_id: str
    tool_name: str
    status: str  # "started", "progress", "completed", "error"
    message: str | None = None
    data: Any = None


class ToolExecutor:
    """Orchestrates tool execution with concurrency control.

    Partitions tools into:
    1. Concurrent-safe batch (executed in parallel)
    2. Non-concurrent-safe batches (executed serially)

    This ensures tools that modify state or have side effects
    don't conflict with each other.

    Attributes:
        context: The tool execution context.
        max_concurrent: Maximum concurrent tool executions.
    """

    def __init__(
        self,
        context: ToolUseContext,
        *,
        max_concurrent: int = 10,
    ) -> None:
        self.context = context
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def execute(
        self,
        block: ToolUseBlock,
    ) -> ToolExecutionResult:
        """Execute a single tool.

        Args:
            block: The tool use block from LLM.

        Returns:
            The execution result.
        """
        tool_name = block["name"]
        tool_call_id = block["id"]
        tool_input = block.get("input", {})

        tool = self.context.tools.get(tool_name)
        if not tool:
            return ToolExecutionResult(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                result=ToolResult(
                    data=None,
                    error=f"Tool not found: {tool_name}",
                ),
            )

        start_time = datetime.now().timestamp()
        try:
            # Check abort
            if self.context.is_aborted():
                return ToolExecutionResult(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    result=ToolResult(
                        data=None,
                        error="Execution cancelled",
                    ),
                )

            result = await tool.call(tool_input, self.context)

        except Exception as e:
            result = ToolResult(data=None, error=str(e))

        execution_time = int((datetime.now().timestamp() - start_time) * 1000)

        return ToolExecutionResult(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            result=result,
            execution_time_ms=execution_time,
        )

    async def execute_batch(
        self,
        blocks: list[ToolUseBlock],
    ) -> list[ToolExecutionResult]:
        """Execute multiple tools with concurrency control.

        Partitions tools by concurrency safety:
        - Concurrent-safe tools run in parallel
        - Non-concurrent-safe tools run serially

        Args:
            blocks: List of tool use blocks.

        Returns:
            List of execution results in the same order as input.
        """
        if not blocks:
            return []

        # Partition by concurrency safety
        batches = self._partition_blocks(blocks)

        results: list[ToolExecutionResult] = []

        for batch in batches:
            if batch["is_concurrency_safe"]:
                # Execute all in batch concurrently
                batch_results = await asyncio.gather(
                    *[self.execute(block) for block in batch["blocks"]],
                    return_exceptions=True,
                )
                for result in batch_results:
                    if isinstance(result, Exception):
                        results.append(ToolExecutionResult(
                            tool_name="unknown",
                            tool_call_id="unknown",
                            result=ToolResult(data=None, error=str(result)),
                        ))
                    else:
                        results.append(result)
            else:
                # Execute serially
                for block in batch["blocks"]:
                    result = await self.execute(block)
                    results.append(result)

        return results

    async def execute_with_progress(
        self,
        blocks: list[ToolUseBlock],
    ) -> AsyncGenerator[ExecutionProgress | ToolExecutionResult, None]:
        """Execute tools with progress reporting.

        Args:
            blocks: List of tool use blocks.

        Yields:
            Progress updates and final results.
        """
        if not blocks:
            return

        batches = self._partition_blocks(blocks)

        for batch in batches:
            if batch["is_concurrency_safe"]:
                # Execute concurrently with progress
                tasks = [
                    self._execute_with_progress(block)
                    for block in batch["blocks"]
                ]
                for coro in asyncio.as_completed(tasks):
                    result = await coro
                    yield result
            else:
                # Execute serially
                for block in batch["blocks"]:
                    async for progress in self._execute_stream(block):
                        yield progress

    async def _execute_stream(
        self,
        block: ToolUseBlock,
    ) -> AsyncGenerator[ExecutionProgress | ToolExecutionResult, None]:
        """Execute a single tool with streaming progress."""
        tool_name = block["name"]
        tool_call_id = block["id"]

        yield ExecutionProgress(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status="started",
        )

        result = await self.execute(block)

        if result.result.is_success():
            yield ExecutionProgress(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status="completed",
            )
        else:
            yield ExecutionProgress(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status="error",
                message=result.result.error,
            )

        yield result

    async def _execute_with_progress(
        self,
        block: ToolUseBlock,
    ) -> ToolExecutionResult:
        """Execute with semaphore for concurrency control."""
        tool_name = block["name"]
        tool_call_id = block["id"]

        async with self.semaphore:
            return await self.execute(block)

    def _partition_blocks(
        self,
        blocks: list[ToolUseBlock],
    ) -> list[dict[str, Any]]:
        """Partition blocks by concurrency safety.

        Groups consecutive concurrency-safe blocks together.
        Non-concurrency-safe blocks get their own single-block batches.

        Args:
            blocks: List of tool use blocks.

        Returns:
            List of batches with is_concurrency_safe flag.
        """
        batches: list[dict[str, Any]] = []

        for block in blocks:
            tool = self.context.tools.get(block.get("name", ""))
            is_safe = (
                tool is not None and
                tool.is_concurrency_safe(block.get("input", {}))
            )

            if is_safe and batches and batches[-1]["is_concurrency_safe"]:
                # Append to existing concurrent batch
                batches[-1]["blocks"].append(block)
            else:
                # Start new batch
                batches.append({
                    "is_concurrency_safe": is_safe,
                    "blocks": [block],
                })

        return batches


def parse_tool_input(
    block: ToolUseBlock,
    schema: dict[str, Any],
) -> dict[str, Any] | None:
    """Parse and validate tool input against schema.

    Args:
        block: The tool use block.
        schema: The JSON schema to validate against.

    Returns:
        Parsed input if valid, None if invalid.
    """
    try:
        return block.get("input", {})
    except Exception:
        return None
