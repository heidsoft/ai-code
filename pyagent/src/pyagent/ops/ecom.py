"""E-commerce operations tools.

Tools for common e-commerce operations:
- Query inventory
- Process orders
- Query order status
- User management
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any

from ..tools.base import Tool, ToolResult, ToolUseContext


class EcomTool(Tool[dict, dict]):
    """E-commerce operations tool.

    Supports:
    - Query inventory
    - Get order details
    - Process order
    - Query user
    - Update order status

    Input schema:
        {
            "action": "inventory" | "order_get" | "order_process" | "order_update" | "user_get",
            "sku": str,              # For inventory
            "order_id": str,        # For order operations
            "quantity": int,        # For order_process
            "user_id": str,          # For user_get
            "status": str,           # For order_update
        }
    """

    name = "ecom_ops"
    description = "E-commerce operations: inventory query, order processing, user lookup."

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["inventory", "order_get", "order_process", "order_update", "user_get"],
                    "description": "The action to perform",
                },
                "sku": {
                    "type": "string",
                    "description": "Product SKU (for inventory query)",
                },
                "order_id": {
                    "type": "string",
                    "description": "Order ID (for order operations)",
                },
                "quantity": {
                    "type": "integer",
                    "description": "Quantity (for order_process)",
                },
                "user_id": {
                    "type": "string",
                    "description": "User ID (for user_get)",
                },
                "status": {
                    "type": "string",
                    "description": "New status (for order_update)",
                },
            },
            "required": ["action"],
        }

    def is_read_only(self, args: dict) -> bool:
        return args.get("action") in ("inventory", "order_get", "user_get")

    def is_concurrency_safe(self, args: dict) -> bool:
        return args.get("action") in ("inventory", "order_get", "user_get")

    async def call(
        self,
        args: dict,
        context: ToolUseContext,
    ) -> ToolResult[dict]:
        """Execute the e-commerce action."""
        action = args.get("action")

        # Get API config from metadata
        api_base = context.metadata.get("ecom_api_base", "")
        api_key = context.metadata.get("ecom_api_key", "")

        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if action == "inventory":
                    return await self._query_inventory(
                        client, headers, api_base, args.get("sku")
                    )
                elif action == "order_get":
                    return await self._get_order(
                        client, headers, api_base, args.get("order_id")
                    )
                elif action == "order_process":
                    return await self._process_order(
                        client, headers, api_base, args.get("order_id"), args.get("quantity")
                    )
                elif action == "order_update":
                    return await self._update_order(
                        client, headers, api_base, args.get("order_id"), args.get("status")
                    )
                elif action == "user_get":
                    return await self._get_user(
                        client, headers, api_base, args.get("user_id")
                    )
                else:
                    return ToolResult(data=None, error=f"Unknown action: {action}")

        except Exception as e:
            return ToolResult(data=None, error=str(e))

    async def _query_inventory(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        sku: str | None,
    ) -> ToolResult[dict]:
        """Query inventory for a SKU."""
        if not sku:
            return ToolResult(data=None, error="sku is required")

        if not api_base:
            return ToolResult(data={
                "sku": sku,
                "available": 150,
                "reserved": 25,
                "warehouse": "WH-001",
                "last_updated": "2026-04-06T12:00:00Z",
            })

        response = await client.get(
            f"{api_base}/inventory/{sku}",
            headers=headers,
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _get_order(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        order_id: str | None,
    ) -> ToolResult[dict]:
        """Get order details."""
        if not order_id:
            return ToolResult(data=None, error="order_id is required")

        if not api_base:
            return ToolResult(data={
                "order_id": order_id,
                "status": "processing",
                "items": [
                    {"sku": "PROD-001", "quantity": 2, "price": 99.99},
                ],
                "total": 199.98,
                "created_at": "2026-04-06T10:00:00Z",
            })

        response = await client.get(
            f"{api_base}/orders/{order_id}",
            headers=headers,
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _process_order(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        order_id: str | None,
        quantity: int | None,
    ) -> ToolResult[dict]:
        """Process an order (reduce inventory, create shipment)."""
        if not order_id:
            return ToolResult(data=None, error="order_id is required")

        if not api_base:
            return ToolResult(data={
                "order_id": order_id,
                "status": "processed",
                "shipment_id": "SHP-001",
                "processed_at": "2026-04-06T12:00:00Z",
            })

        response = await client.post(
            f"{api_base}/orders/{order_id}/process",
            headers=headers,
            json={"quantity": quantity} if quantity else {},
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _update_order(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        order_id: str | None,
        status: str | None,
    ) -> ToolResult[dict]:
        """Update order status."""
        if not order_id:
            return ToolResult(data=None, error="order_id is required")
        if not status:
            return ToolResult(data=None, error="status is required")

        if not api_base:
            return ToolResult(data={
                "order_id": order_id,
                "status": status,
                "updated_at": "2026-04-06T12:00:00Z",
            })

        response = await client.patch(
            f"{api_base}/orders/{order_id}",
            headers=headers,
            json={"status": status},
        )
        response.raise_for_status()
        return ToolResult(data=response.json())

    async def _get_user(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        user_id: str | None,
    ) -> ToolResult[dict]:
        """Get user details."""
        if not user_id:
            return ToolResult(data=None, error="user_id is required")

        if not api_base:
            return ToolResult(data={
                "user_id": user_id,
                "email": "user@example.com",
                "name": "Test User",
                "tier": "gold",
                "orders_count": 42,
            })

        response = await client.get(
            f"{api_base}/users/{user_id}",
            headers=headers,
        )
        response.raise_for_status()
        return ToolResult(data=response.json())
