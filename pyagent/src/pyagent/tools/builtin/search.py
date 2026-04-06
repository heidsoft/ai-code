"""Search tool for web search."""

from __future__ import annotations

import httpx
from dataclasses import dataclass

from ..base import Tool, ToolResult, ToolUseContext


@dataclass
class SearchTool(Tool[dict, str]):
    """Search the web.

    Uses DuckDuckGo HTML for lightweight search.

    Input schema:
        {
            "query": str,
            "num_results": int = 5,
        }
    """

    name = "search"
    description = "Search the web for information. Use when you need current info or don't know something."

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    async def call(
        self,
        args: dict,
        context: ToolUseContext,
    ) -> ToolResult[str]:
        """Execute the search."""
        query = args.get("query", "")
        num_results = args.get("num_results", 5)

        if not query:
            return ToolResult(data=None, error="No query provided")

        try:
            # Use DuckDuckGo HTML search (lightweight)
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                )

            if response.status_code != 200:
                return ToolResult(
                    data=None,
                    error=f"Search failed with status {response.status_code}",
                )

            # Parse results (simple HTML parsing)
            results = self._parse_results(response.text, num_results)

            if not results:
                return ToolResult(data="No results found")

            output = "\n\n".join(
                f"{i+1}. {r['title']}\n   {r['snippet']}\n   {r['url']}"
                for i, r in enumerate(results)
            )

            return ToolResult(data=output)

        except Exception as e:
            return ToolResult(data=None, error=str(e))

    def _parse_results(self, html: str, num_results: int) -> list[dict]:
        """Parse search results from DuckDuckGo HTML."""
        import re

        results = []

        # Simple regex-based parsing
        # Find result blocks
        result_pattern = re.compile(
            r'<a class="result__a" href="([^"]+)">([^<]+)</a>.*?'
            r'<a class="result__snippet"[^>]*>([^<]+)</a>',
            re.DOTALL,
        )

        for match in result_pattern.finditer(html):
            url = match.group(1)
            title = match.group(2).strip()
            snippet = match.group(3).strip()
            snippet = re.sub(r'<[^>]+>', '', snippet)  # Remove tags

            results.append({
                "title": title,
                "snippet": snippet[:200],
                "url": url,
            })

            if len(results) >= num_results:
                break

        return results
