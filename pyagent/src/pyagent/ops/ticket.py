"""Ticket handling tools for autoOps.

Tools for managing tickets from ITSM systems:
- List tickets
- Create tickets
- Update ticket status
- Add comments
- Close tickets
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any

from ..tools.base import Tool, ToolResult, ToolUseContext


@dataclass
class Ticket:
    """Represents a ticket."""

    ticket_id: str
    title: str
    description: str
    status: str  # open, in_progress, resolved, closed
    priority: str  # critical, high, medium, low
    assignee: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TicketTool(Tool[dict, dict]):
    """Handle tickets from ITSM systems.

    Supports:
    - List tickets
    - Get ticket details
    - Create tickets
    - Update ticket status
    - Add comments
    - Close tickets

    Input schema:
        {
            "action": "list" | "get" | "create" | "update" | "comment" | "close",
            "ticket_id": str,         # Required for non-create/list actions
            "title": str,              # For create
            "description": str,        # For create
            "priority": str,           # For create/update
            "status": str,             # For update
            "comment": str,            # For comment
        }
    """

    name = "ticket_handler"
    description = "Manage ITSM tickets: list, get, create, update status, add comments, close."

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "create", "update", "comment", "close"],
                    "description": "The action to perform",
                },
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket ID",
                },
                "title": {
                    "type": "string",
                    "description": "Ticket title (for create)",
                },
                "description": {
                    "type": "string",
                    "description": "Ticket description (for create)",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low"],
                    "description": "Ticket priority",
                },
                "status": {
                    "type": "string",
                    "enum": ["open", "in_progress", "resolved", "closed"],
                    "description": "Ticket status (for update)",
                },
                "comment": {
                    "type": "string",
                    "description": "Comment text (for comment action)",
                },
            },
            "required": ["action"],
        }

    def is_read_only(self, args: dict) -> bool:
        return args.get("action") in ("list", "get")

    def is_concurrency_safe(self, args: dict) -> bool:
        return args.get("action") in ("list", "get")

    async def call(
        self,
        args: dict,
        context: ToolUseContext,
    ) -> ToolResult[dict]:
        """Execute the ticket action."""
        action = args.get("action")
        ticket_id = args.get("ticket_id")

        # Get API config from metadata
        api_base = context.metadata.get("ticket_api_base", "")
        api_key = context.metadata.get("ticket_api_key", "")

        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if action == "list":
                    return await self._list_tickets(client, headers, api_base)
                elif action == "get":
                    return await self._get_ticket(client, headers, api_base, ticket_id)
                elif action == "create":
                    return await self._create_ticket(
                        client, headers, api_base, args.get("title"), args.get("description"), args.get("priority")
                    )
                elif action == "update":
                    return await self._update_ticket(
                        client, headers, api_base, ticket_id, args.get("status"), args.get("priority")
                    )
                elif action == "comment":
                    return await self._add_comment(
                        client, headers, api_base, ticket_id, args.get("comment")
                    )
                elif action == "close":
                    return await self._close_ticket(client, headers, api_base, ticket_id)
                else:
                    return ToolResult(data=None, error=f"Unknown action: {action}")

        except Exception as e:
            return ToolResult(data=None, error=str(e))

    async def _list_tickets(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
    ) -> ToolResult[dict]:
        """List open tickets."""
        if not api_base:
            return ToolResult(data={
                "tickets": [
                    {
                        "ticket_id": "TKT001",
                        "title": "Database connection issues",
                        "status": "open",
                        "priority": "high",
                    },
                    {
                        "ticket_id": "TKT002",
                        "title": "User cannot login",
                        "status": "in_progress",
                        "priority": "critical",
                    },
                ]
            })

        response = await client.get(f"{api_base}/tickets", headers=headers)
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _get_ticket(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        ticket_id: str,
    ) -> ToolResult[dict]:
        """Get ticket details."""
        if not ticket_id:
            return ToolResult(data=None, error="ticket_id is required")

        if not api_base:
            return ToolResult(data={
                "ticket_id": ticket_id,
                "title": "Sample ticket",
                "description": "This is a sample ticket",
                "status": "open",
                "priority": "high",
                "assignee": "admin",
            })

        response = await client.get(f"{api_base}/tickets/{ticket_id}", headers=headers)
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _create_ticket(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        title: str,
        description: str,
        priority: str,
    ) -> ToolResult[dict]:
        """Create a new ticket."""
        if not title:
            return ToolResult(data=None, error="title is required for create")

        if not api_base:
            return ToolResult(data={
                "ticket_id": "TKT003",
                "title": title,
                "description": description or "",
                "status": "open",
                "priority": priority or "medium",
                "created_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/tickets",
            headers=headers,
            json={
                "title": title,
                "description": description,
                "priority": priority or "medium",
            },
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _update_ticket(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        ticket_id: str,
        status: str | None,
        priority: str | None,
    ) -> ToolResult[dict]:
        """Update ticket status or priority."""
        if not ticket_id:
            return ToolResult(data=None, error="ticket_id is required")

        if not api_base:
            return ToolResult(data={
                "ticket_id": ticket_id,
                "status": status or "in_progress",
                "priority": priority,
                "updated_at": "2026-04-06T12:00:00Z",
            })

        payload = {}
        if status:
            payload["status"] = status
        if priority:
            payload["priority"] = priority

        response = await client.patch(
            f"{api_base}/tickets/{ticket_id}",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _add_comment(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        ticket_id: str,
        comment: str,
    ) -> ToolResult[dict]:
        """Add a comment to a ticket."""
        if not ticket_id:
            return ToolResult(data=None, error="ticket_id is required")
        if not comment:
            return ToolResult(data=None, error="comment text is required")

        if not api_base:
            return ToolResult(data={
                "ticket_id": ticket_id,
                "comment_id": "C001",
                "comment": comment,
                "created_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/tickets/{ticket_id}/comments",
            headers=headers,
            json={"comment": comment},
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _close_ticket(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        ticket_id: str,
    ) -> ToolResult[dict]:
        """Close a ticket."""
        if not ticket_id:
            return ToolResult(data=None, error="ticket_id is required")

        if not api_base:
            return ToolResult(data={
                "ticket_id": ticket_id,
                "status": "closed",
                "closed_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/tickets/{ticket_id}/close",
            headers=headers,
        )
        response.raise_for_status()
        return ToolResult(data=response.json())
