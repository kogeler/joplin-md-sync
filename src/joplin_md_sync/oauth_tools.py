"""Expose the common registry and executor through FastMCP's provider API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastmcp.server.providers import Provider
from fastmcp.tools import Tool, ToolResult
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import PrivateAttr

from joplin_md_sync.mcp_server import McpDispatcher
from joplin_md_sync.oauth_execution import CapacityExhausted, ExecutionBudget
from joplin_md_sync.tool_registry import ToolDefinition


class RegistryTool(Tool):
    _dispatcher: McpDispatcher = PrivateAttr()
    _budget: ExecutionBudget = PrivateAttr()

    def __init__(
        self, definition: ToolDefinition, dispatcher: McpDispatcher, budget: ExecutionBudget
    ) -> None:
        raw = definition.to_mcp_json()
        super().__init__(
            name=raw["name"],
            title=raw["title"],
            description=raw["description"],
            parameters=raw["inputSchema"],
            output_schema=raw.get("outputSchema"),
            annotations=ToolAnnotations.model_validate(raw["annotations"]),
        )
        self._dispatcher = dispatcher
        self._budget = budget

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            payload = await self._budget.run(
                self._dispatcher._call_tool, {"name": self.name, "arguments": arguments}
            )
        except CapacityExhausted:
            payload = self._dispatcher._tool_error(
                "The service is busy.", code="CAPACITY_EXHAUSTED", retryable=True
            )
        return ToolResult.from_mcp_result(CallToolResult.model_validate(payload))


class RegistryProvider(Provider):
    def __init__(self, dispatcher: McpDispatcher, budget: ExecutionBudget) -> None:
        super().__init__()
        self.tools = tuple(
            RegistryTool(definition, dispatcher, budget)
            for definition in dispatcher.registry.definitions
        )

    async def _list_tools(self) -> Sequence[Tool]:
        return self.tools
