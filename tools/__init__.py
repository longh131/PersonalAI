"""Action system: tool registry, built-ins, schemas, and MCP reserve."""

from tools.builtin import BuiltinTools, looks_like_image_path
from tools.mcp import MCPBridge
from tools.registry import ToolRegistry
from tools.schemas import TOOL_SCHEMAS

__all__ = [
    "BuiltinTools",
    "MCPBridge",
    "TOOL_SCHEMAS",
    "ToolRegistry",
    "looks_like_image_path",
]


def verify_module() -> None:
    """Confirm required tool schemas exist."""
    from tools.builtin import verify_module as verify_builtin

    verify_builtin()
    assert ToolRegistry().names() == []
