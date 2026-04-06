"""Anthropic Claude client implementation."""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from anthropic import AsyncAnthropic
from anthropic.types import (
    Message,
    MessageCreateParams,
    TextBlock,
    ToolUseBlock,
    ContentBlock,
)

from .base import LLMClient, LLMResponse, MessageParam, StreamEvent, ToolUse
from ..core.types import Usage


class AnthropicClient(LLMClient):
    """Anthropic Claude API client.

    Supports Claude models with streaming and tool use.

    Example:
        ```python
        client = AnthropicClient(api_key="sk-ant-...")
        tools = [{"name": "bash", "description": "...", "input_schema": {...}}]

        async for event in client.stream(messages, tools):
            if isinstance(event, LLMResponse):
                print(event.content, end="")
            elif isinstance(event, ToolUse):
                print(f"Tool: {event.name}")
        ```
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "claude-sonnet-4-20250514",
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.base_url = base_url

        self._client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.base_url,
        )

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
        """Generate a complete response."""
        # Convert messages
        api_messages = self._convert_messages(messages)

        # Build params
        params: dict[str, Any] = {
            "model": model or self.model,
            "messages": api_messages,
            "temperature": temperature,
        }

        if system_prompt:
            params["system"] = system_prompt

        if tools:
            params["tools"] = tools

        if max_tokens:
            params["max_tokens"] = max_tokens
        else:
            # Use default for non-streaming
            params["max_tokens"] = 4096

        message = await self._client.messages.create(**params)

        return self._convert_message(message)

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
        """Stream a response with tool use support."""
        import json as json_module

        api_messages = self._convert_messages(messages)

        params: dict[str, Any] = {
            "model": model or self.model,
            "messages": api_messages,
            "temperature": temperature,
        }

        if system_prompt:
            params["system"] = system_prompt

        if tools:
            params["tools"] = tools

        if max_tokens:
            params["max_tokens"] = max_tokens
        else:
            # Required for streaming, use a reasonable default
            params["max_tokens"] = 4096

        tool_uses: list[ToolUse] = []
        text_content: list[str] = []
        # For accumulating partial JSON input
        pending_input_str: str = ""
        pending_tool_use_index: int | None = None

        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        block = event.content_block
                        if hasattr(block, "type") and block.type == "tool_use":
                            tool_block = block
                            tool_uses.append(ToolUse(
                                id=tool_block.id,
                                name=tool_block.name,
                                input=tool_block.input or {},
                            ))
                            pending_tool_use_index = len(tool_uses) - 1
                            pending_input_str = ""
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta"):
                        delta = event.delta
                        if hasattr(delta, "text"):
                            text_content.append(delta.text)
                            yield LLMResponse(content=delta.text)
                        elif hasattr(delta, "partial_json") and pending_tool_use_index is not None:
                            pending_input_str += delta.partial_json
                            print(f"[DEBUG] partial_json: '{delta.partial_json}', accumulated: '{pending_input_str}'")
                            # Try to parse accumulated JSON
                            try:
                                parsed = json_module.loads(pending_input_str)
                                print(f"[DEBUG] Parsed successfully: {parsed}")
                                if isinstance(parsed, dict):
                                    tool_uses[pending_tool_use_index] = ToolUse(
                                        id=tool_uses[pending_tool_use_index].id,
                                        name=tool_uses[pending_tool_use_index].name,
                                        input=parsed,
                                    )
                            except json_module.JSONDecodeError as e:
                                print(f"[DEBUG] Parse failed: {e}")
                                # Not complete yet, keep accumulating
                                pass
                elif event.type == "content_block_stop":
                    # Try one final parse in case the last delta completed the JSON
                    if pending_tool_use_index is not None:
                        if pending_input_str:
                            try:
                                parsed = json_module.loads(pending_input_str)
                                if isinstance(parsed, dict):
                                    tool_uses[pending_tool_use_index] = ToolUse(
                                        id=tool_uses[pending_tool_use_index].id,
                                        name=tool_uses[pending_tool_use_index].name,
                                        input=parsed,
                                    )
                            except json_module.JSONDecodeError:
                                pass
                        # Yield the tool use (even if parsing incomplete)
                        yield tool_uses[pending_tool_use_index]
                    pending_tool_use_index = None
                    pending_input_str = ""
                elif event.type == "message_delta":
                    # Final message metadata
                    pass
                elif event.type == "message_stop":
                    # Stream complete
                    pass

        # If no tool uses, yield final response
        if not tool_uses and text_content:
            yield LLMResponse(content="".join(text_content))

    def _convert_messages(
        self,
        messages: list[MessageParam],
    ) -> list[dict[str, Any]]:
        """Convert MessageParams to API format."""
        result = []
        for msg in messages:
            content: str | list[dict[str, Any]] = msg.content

            # Handle tool results (role can be 'user' or 'tool')
            if msg.tool_call_id:
                result.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content or "",
                    }],
                })
            elif msg.tool_calls:
                # Assistant message with tool calls
                result.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                })
            else:
                result.append({
                    "role": msg.role,
                    "content": content,
                })

        # Debug output for second call
        print(f"[DEBUG] _convert_messages: {result}")
        return result

    def _convert_message(self, message: Message) -> LLMResponse:
        """Convert API message to LLMResponse."""
        text_parts = []
        tool_uses = []

        for block in message.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_uses.append(ToolUse(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return LLMResponse(
            content="".join(text_parts),
            tool_uses=tool_uses if tool_uses else None,
            stop_reason=str(message.stop_reason) if message.stop_reason else None,
            usage=Usage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ) if hasattr(message, 'usage') and message.usage else None,
        )
