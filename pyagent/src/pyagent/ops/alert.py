"""Alert handling tools for autoOps.

Tools for handling alerts from monitoring systems:
- Acknowledge alerts
- Escalate alerts
- Reassign alerts
- Create incidents from alerts
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any

from ..tools.base import Tool, ToolResult, ToolUseContext


@dataclass
class Alert:
    """Represents an alert."""

    alert_id: str
    severity: str  # critical, high, medium, low
    title: str
    message: str
    source: str
    status: str = "firing"  # firing, acknowledged, resolved
    assignee: str | None = None
    created_at: str | None = None


class AlertTool(Tool[dict, dict]):
    """Handle alerts from monitoring systems.

    Supports:
    - List alerts
    - Acknowledge alerts
    - Escalate alerts
    - Reassign alerts
    - Create incidents

    Input schema:
        {
            "action": "list" | "ack" | "escalate" | "reassign" | "create_incident",
            "alert_id": str,           # Required for non-list actions
            "severity": str,           # For create_incident
            "assignee": str,           # For reassign
            "incident_title": str,     # For create_incident
        }
    """

    name = "alert_handler"
    description = "Handle monitoring alerts: list, acknowledge, escalate, reassign, or create incidents."

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "ack", "escalate", "reassign", "create_incident"],
                    "description": "The action to perform",
                },
                "alert_id": {
                    "type": "string",
                    "description": "The alert ID (required for actions except 'list')",
                },
                "severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low"],
                    "description": "Alert severity (for create_incident)",
                },
                "assignee": {
                    "type": "string",
                    "description": "Person to assign the alert to",
                },
                "incident_title": {
                    "type": "string",
                    "description": "Title for the incident (create_incident only)",
                },
            },
            "required": ["action"],
        }

    def is_read_only(self, args: dict) -> bool:
        return args.get("action") == "list"

    def is_concurrency_safe(self, args: dict) -> bool:
        return args.get("action") == "list"

    async def call(
        self,
        args: dict,
        context: ToolUseContext,
    ) -> ToolResult[dict]:
        """Execute the alert action."""
        action = args.get("action")
        alert_id = args.get("alert_id")

        # Get API config from metadata
        api_base = context.metadata.get("alert_api_base", "")
        api_key = context.metadata.get("alert_api_key", "")

        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if action == "list":
                    return await self._list_alerts(client, headers, api_base)
                elif action == "ack":
                    return await self._ack_alert(client, headers, api_base, alert_id)
                elif action == "escalate":
                    return await self._escalate_alert(client, headers, api_base, alert_id)
                elif action == "reassign":
                    return await self._reassign_alert(
                        client, headers, api_base, alert_id, args.get("assignee")
                    )
                elif action == "create_incident":
                    return await self._create_incident(
                        client, headers, api_base, alert_id, args.get("severity"), args.get("incident_title")
                    )
                else:
                    return ToolResult(data=None, error=f"Unknown action: {action}")

        except Exception as e:
            return ToolResult(data=None, error=str(e))

    async def _list_alerts(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
    ) -> ToolResult[dict]:
        """List all firing alerts."""
        if not api_base:
            # Demo mode - return sample alerts
            return ToolResult(data={
                "alerts": [
                    {
                        "alert_id": "A001",
                        "severity": "critical",
                        "title": "High CPU Usage",
                        "message": "CPU > 90% for 5 minutes",
                        "source": "prometheus",
                        "status": "firing",
                    },
                    {
                        "alert_id": "A002",
                        "severity": "high",
                        "title": "Memory Pressure",
                        "message": "Memory > 85%",
                        "source": "datadog",
                        "status": "firing",
                    },
                ]
            })

        response = await client.get(f"{api_base}/alerts", headers=headers)
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _ack_alert(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        alert_id: str,
    ) -> ToolResult[dict]:
        """Acknowledge an alert."""
        if not api_base:
            return ToolResult(data={
                "status": "acknowledged",
                "alert_id": alert_id,
                "acknowledged_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/alerts/{alert_id}/ack",
            headers=headers,
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _escalate_alert(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        alert_id: str,
    ) -> ToolResult[dict]:
        """Escalate an alert."""
        if not api_base:
            return ToolResult(data={
                "status": "escalated",
                "alert_id": alert_id,
                "escalated_to": "on-call-manager",
                "escalated_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/alerts/{alert_id}/escalate",
            headers=headers,
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _reassign_alert(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        alert_id: str,
        assignee: str,
    ) -> ToolResult[dict]:
        """Reassign an alert to someone else."""
        if not assignee:
            return ToolResult(data=None, error="Assignee is required")

        if not api_base:
            return ToolResult(data={
                "status": "reassigned",
                "alert_id": alert_id,
                "assignee": assignee,
                "reassigned_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/alerts/{alert_id}/reassign",
            headers=headers,
            json={"assignee": assignee},
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _create_incident(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        alert_id: str | None,
        severity: str,
        title: str,
    ) -> ToolResult[dict]:
        """Create an incident from an alert."""
        if not api_base:
            return ToolResult(data={
                "incident_id": "INC001",
                "title": title or f"Incident from alert {alert_id}",
                "severity": severity or "high",
                "status": "open",
                "created_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/incidents",
            headers=headers,
            json={
                "alert_id": alert_id,
                "severity": severity,
                "title": title,
            },
        )
        response.raise_for_status()
        return ToolResult(data=response.json())
