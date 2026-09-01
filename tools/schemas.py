"""JSON schemas for built-in tools (OpenAI function-calling format)."""

from __future__ import annotations

from typing import Any


def openai_tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build one OpenAI-style tool definition."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    openai_tool(
        "read_file",
        "读取本地文本文件内容。",
        {"path": {"type": "string", "description": "文件路径，相对工作区或绝对路径。"}},
        ["path"],
    ),
    openai_tool(
        "search_files",
        "按 glob 模式在工作区内搜索文件。",
        {
            "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
            "root": {"type": "string", "description": "可选搜索根目录。"},
        },
        ["pattern"],
    ),
    openai_tool(
        "web_search",
        "在互联网上搜索公开资料，返回标题、链接和摘要。",
        {
            "query": {"type": "string", "description": "搜索查询"},
            "max_results": {"type": "integer", "description": "返回条数，默认 5"},
        },
        ["query"],
    ),
    openai_tool(
        "execute_code",
        "在隔离临时目录中执行短 Python 代码，仅用于计算和文本处理。",
        {"code": {"type": "string", "description": "要执行的 Python 源码"}},
        ["code"],
    ),
    openai_tool(
        "capture_screen",
        "截取当前屏幕并做 OCR / 图像分析，适合用户说「看看屏幕」。",
        {"monitor": {"type": "integer", "description": "显示器序号，默认 1"}},
        [],
    ),
    openai_tool(
        "analyze_image",
        "读取一张本地图片，进行 OCR 并描述可见内容。",
        {"path": {"type": "string", "description": "图片路径"}},
        ["path"],
    ),
]


def schema_by_name() -> dict[str, dict[str, Any]]:
    """Index tool schemas by function name."""
    return {item["function"]["name"]: item for item in TOOL_SCHEMAS}
