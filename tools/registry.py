"""Tool registry: register, list OpenAI schemas, and execute calls."""

from __future__ import annotations

import inspect
from typing import Any, Protocol

from loguru import logger


class BaseTool(Protocol):
    """Callable tool with an OpenAI function schema."""

    name: str
    description: str
    schema: dict[str, Any]

    async def run(self, arguments: dict[str, Any]) -> str:
        """Execute the tool and return a string payload for the LLM."""


class FunctionTool:
    """Adapter that wraps an async function as a `BaseTool`."""

    def __init__(self, name: str, description: str, schema: dict[str, Any], handler: Any) -> None:
        self.name = name
        self.description = description
        self.schema = schema
        self._handler = handler

    async def run(self, arguments: dict[str, Any]) -> str:
        """Invoke the wrapped handler with unpacked arguments.

        Extra keys from the model are ignored; missing optional params keep defaults.
        """
        signature = inspect.signature(self._handler)
        kwargs: dict[str, Any] = {}
        for name, param in signature.parameters.items():
            if name in arguments:
                kwargs[name] = arguments[name]
            elif param.default is inspect.Parameter.empty and param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                raise TypeError(f"Tool {self.name} missing argument: {name}")
        result = await self._handler(**kwargs)
        return str(result)


class ToolRegistry:
    """Name → tool map used by the execute_tools graph node."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register or replace a tool by name."""
        self._tools[tool.name] = tool
        logger.debug("Registered tool {}", tool.name)

    def get(self, name: str) -> BaseTool:
        """Return a registered tool.

        Raises:
            KeyError: If the tool is unknown.
        """
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_openai_tools(self) -> list[dict[str, Any]]:
        """Return OpenAI/DeepSeek function-calling schemas."""
        return [tool.schema for tool in self._tools.values()]

    async def execute(self, call: dict[str, Any]) -> dict[str, Any]:
        """Run one tool call and always return a structured result dict."""
        name = str(call.get("name") or "")
        call_id = str(call.get("id") or "")
        arguments = call.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {"value": arguments}
        try:
            tool = self.get(name)
            output = await tool.run(arguments)
            logger.info("Tool {} ok", name)
            return {"tool_call_id": call_id, "name": name, "ok": True, "output": output}
        except Exception as exc:  # noqa: BLE001 - tool failures must not kill the graph
            logger.exception("Tool {} failed", name)
            return {
                "tool_call_id": call_id,
                "name": name,
                "ok": False,
                "output": "",
                "error": str(exc),
            }

    async def execute_many(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Execute tool calls sequentially (order-preserving, safer for files)."""
        results: list[dict[str, Any]] = []
        for call in calls:
            results.append(await self.execute(call))
        return results

    def names(self) -> list[str]:
        """Return registered tool names."""
        return sorted(self._tools)

    async def self_check(self) -> dict[str, bool]:
        """Verify that the four core tools are registered."""
        required = {"read_file", "search_files", "web_search", "execute_code"}
        return {name: name in self._tools for name in required}
