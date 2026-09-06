"""Desktop app aliases and launch safety."""

from __future__ import annotations

import pytest

from tools.desktop import is_unsafe_launch, resolve_app_target


def test_resolve_aliases() -> None:
    assert resolve_app_target("记事本") == "notepad.exe"
    assert resolve_app_target("计算器") == "calc.exe"
    assert resolve_app_target("chrome") == "chrome.exe"


def test_unsafe_launch_rejected() -> None:
    assert is_unsafe_launch("shutdown /s")
    assert is_unsafe_launch("cmd /c del /f")
    assert is_unsafe_launch("notepad && calc")
    assert not is_unsafe_launch("notepad.exe")
    assert not is_unsafe_launch("https://example.com")


@pytest.mark.asyncio
async def test_desktop_tools_registered(tmp_path) -> None:
    from pathlib import Path

    from config.settings import Settings
    from tools.builtin import BuiltinTools
    from tools.registry import ToolRegistry

    settings = Settings(
        sqlite_path=str(tmp_path / "x.db"),
        workspace_root=str(Path(__file__).resolve().parents[1]),
        deepseek_api_key="x",
    )
    registry = ToolRegistry()
    BuiltinTools(settings, vision=None).register_all(registry)
    for name in (
        "foreground_window",
        "clipboard_text",
        "list_windows",
        "open_app",
        "focus_window",
        "calendar_agenda",
        "set_reminder",
        "list_reminders",
        "daily_briefing",
        "background_task",
        "set_volume",
        "lock_pc",
        "delete_file",
        "power_action",
    ):
        assert name in registry.names()
    denied = await registry.execute(
        {"id": "x", "name": "open_app", "arguments": {"target": "shutdown /s"}}
    )
    assert denied["ok"] is True
    assert "拒绝" in denied["output"]
