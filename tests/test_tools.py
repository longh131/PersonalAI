"""Built-in tools and the registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from tools.builtin import BuiltinTools
from tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_registry_core_tools(tmp_path: Path) -> None:
    """The four required tools register and execute_code can print."""
    settings = Settings(
        sqlite_path=str(tmp_path / "x.db"),
        workspace_root=str(Path(__file__).resolve().parents[1]),
        deepseek_api_key="x",
    )
    registry = ToolRegistry()
    BuiltinTools(settings, vision=None).register_all(registry)
    check = await registry.self_check()
    assert all(check.values())
    result = await registry.execute(
        {"id": "1", "name": "execute_code", "arguments": {"code": "print(1+1)"}}
    )
    assert result["ok"] is True
    assert "2" in result["output"]


@pytest.mark.asyncio
async def test_read_and_search_files(tmp_path: Path) -> None:
    """read_file and search_files operate inside the workspace."""
    settings = Settings(
        sqlite_path=str(tmp_path / "x.db"),
        workspace_root=str(Path(__file__).resolve().parents[1]),
        deepseek_api_key="x",
    )
    registry = ToolRegistry()
    BuiltinTools(settings, vision=None).register_all(registry)
    listed = await registry.execute(
        {"id": "2", "name": "search_files", "arguments": {"pattern": "main.py"}}
    )
    assert listed["ok"] is True
    assert "main.py" in listed["output"]
    read = await registry.execute(
        {"id": "3", "name": "read_file", "arguments": {"path": "main.py"}}
    )
    assert read["ok"] is True
    assert "PersonalAIEngine" in read["output"] or "async_main" in read["output"]


@pytest.mark.asyncio
async def test_execute_code_denies_dangerous_calls(tmp_path: Path) -> None:
    """Destructive patterns are rejected before a subprocess starts."""
    settings = Settings(sqlite_path=str(tmp_path / "x.db"), deepseek_api_key="x")
    registry = ToolRegistry()
    BuiltinTools(settings, vision=None).register_all(registry)
    result = await registry.execute(
        {
            "id": "4",
            "name": "execute_code",
            "arguments": {"code": "import os\nos.system('echo hi')"},
        }
    )
    assert result["ok"] is True
    assert "拒绝执行" in result["output"]
