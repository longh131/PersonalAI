"""Reserved MCP bridge. Disabled until MCP_SERVER_URL is configured."""

from __future__ import annotations

from typing import Any

from loguru import logger


class MCPBridge:
    """Placeholder MCP client that never silently pretends to have tools.

    When `server_url` is empty the bridge is disabled and returns no tools.
    When a URL is provided, a JSON-RPC `tools/list` probe is attempted; failures
    are logged and treated as "no extra tools".
    """

    def __init__(self, server_url: str = "") -> None:
        self.server_url = (server_url or "").strip()
        self.enabled = bool(self.server_url)

    async def list_tools(self) -> list[dict[str, Any]]:
        """List remote MCP tools. Returns an empty list when disabled or unreachable."""
        if not self.enabled:
            return []
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    self.server_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                )
                response.raise_for_status()
                payload = response.json()
            return list((payload.get("result") or {}).get("tools") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP list_tools failed: {}", exc)
            return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a remote MCP tool or explain that MCP is not configured."""
        if not self.enabled:
            return "MCP 未配置：请在 .env 中设置 MCP_SERVER_URL 后再使用外部 MCP 工具。"
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.server_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                )
                response.raise_for_status()
                payload = response.json()
            return str(payload.get("result") or payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP call_tool failed: {}", exc)
            return f"MCP 调用失败：{exc}"
