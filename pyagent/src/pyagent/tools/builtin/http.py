"""HTTP tool for making web requests.

Based on Claude Code's WebFetchTool:
- GET/POST requests
- JSON parsing
- Headers support
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass

from ..base import Tool, ToolResult, ToolUseContext


@dataclass
class HttpTool(Tool[dict, str]):
    """Make HTTP requests.

    Supports GET and POST requests with headers and JSON body.

    Input schema:
        {
            "method": "GET" | "POST",
            "url": str,
            "headers": dict = {},
            "body": dict = None,
            "timeout": int = 30,
        }
    """

    name = "http"
    description = "Make HTTP requests. Use for API calls, fetching web content, and webhooks."

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                    "description": "HTTP method",
                    "default": "GET",
                },
                "url": {
                    "type": "string",
                    "description": "The URL to request",
                },
                "headers": {
                    "type": "object",
                    "description": "Request headers",
                    "additionalProperties": {"type": "string"},
                },
                "body": {
                    "type": "object",
                    "description": "JSON body for POST/PUT/PATCH",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 30,
                },
            },
            "required": ["url"],
        }

    def is_read_only(self, args: dict) -> bool:
        """GET requests are read-only."""
        return args.get("method", "GET").upper() == "GET"

    async def call(
        self,
        args: dict,
        context: ToolUseContext,
    ) -> ToolResult[str]:
        """Execute the HTTP request."""
        method = args.get("method", "GET").upper()
        url = args.get("url")
        headers = args.get("headers", {})
        body = args.get("body")
        timeout = args.get("timeout", 30)

        if not url:
            return ToolResult(data=None, error="No URL provided")

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            return ToolResult(data=None, error="Invalid URL scheme")

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                elif method == "POST":
                    response = await client.post(url, headers=headers, json=body)
                elif method == "PUT":
                    response = await client.put(url, headers=headers, json=body)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers)
                elif method == "PATCH":
                    response = await client.patch(url, headers=headers, json=body)
                else:
                    return ToolResult(data=None, error=f"Unsupported method: {method}")

                # Try to parse as JSON
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    try:
                        json_data = response.json()
                        import json
                        return ToolResult(data=json.dumps(json_data, indent=2))
                    except Exception:
                        pass

                return ToolResult(
                    data=response.text[:10000],  # Limit response size
                    metadata={
                        "status_code": response.status_code,
                        "headers": dict(response.headers),
                    },
                )

        except httpx.TimeoutException:
            return ToolResult(data=None, error=f"Request timed out after {timeout}s")
        except httpx.RequestError as e:
            return ToolResult(data=None, error=f"Request failed: {e}")
        except Exception as e:
            return ToolResult(data=None, error=str(e))
