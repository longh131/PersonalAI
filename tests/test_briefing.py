"""Morning/leaving briefings, background jobs, and confirmed desktop actions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from memory.reminders import Reminder
from tools.briefing import auto_briefing_kind, compose_briefing
from tools.jobs import JobQueue


def test_auto_briefing_kind_windows() -> None:
    morning = datetime(2026, 9, 2, 8, 0)
    noon = datetime(2026, 9, 2, 13, 0)
    evening = datetime(2026, 9, 2, 18, 30)
    night = datetime(2026, 9, 2, 23, 30)
    assert auto_briefing_kind(morning) == "morning"
    assert auto_briefing_kind(noon) is None
    assert auto_briefing_kind(evening) == "leaving"
    assert auto_briefing_kind(night) is None


@pytest.mark.asyncio
async def test_compose_briefing_skips_healthy_status() -> None:
    reminder = Reminder(id=1, message="回邮件", due_at="2026-09-02T10:00:00+08:00", status="pending")
    text = await compose_briefing(
        "morning",
        reminders=[reminder],
        agenda="今天 10:00 站会",
        status="电量：80%（充电中）\n网络：通\n磁盘 C:\\ 剩余 80.0 GB",
    )
    assert "晨间简报" in text
    assert "站会" in text
    assert "回邮件" in text
    assert "80.0 GB" not in text
    assert "充电中" not in text

    leaving = await compose_briefing(
        "leaving",
        reminders=[],
        agenda="明天 09:00 一对一",
        status="电量：12%（未插电）\n网络：不通\n磁盘 C:\\ 剩余 2.0 GB",
    )
    assert "离开简报" in leaving
    assert "一对一" in leaving
    assert "未插电" in leaving
    assert "网络不通" in leaving
    assert "2.0 GB" in leaving


def test_job_queue_fifo() -> None:
    jobs = JobQueue()
    assert jobs.pop_nowait() is None
    ack = jobs.submit("搜索公开资料并写摘要")
    assert "后台" in ack
    assert jobs.pending_count() == 1
    assert jobs.pop_nowait() == "搜索公开资料并写摘要"
    assert jobs.pop_nowait() is None


@pytest.mark.asyncio
async def test_delete_and_power_need_spoken_confirm(tmp_path: Path) -> None:
    from config.settings import Settings
    from tools.builtin import BuiltinTools

    settings = Settings(
        sqlite_path=str(tmp_path / "x.db"),
        workspace_root=str(tmp_path),
        deepseek_api_key="x",
    )
    target = tmp_path / "gone.txt"
    target.write_text("keep", encoding="utf-8")
    tools = BuiltinTools(settings)

    tools.last_user_text = "删掉 gone.txt"
    denied = await tools.delete_file("gone.txt", confirm=False)
    assert "确认" in denied
    assert target.exists()

    tools.last_user_text = "删掉 gone.txt"
    still = await tools.delete_file("gone.txt", confirm=True)
    assert "确认" in still
    assert target.exists()

    tools.last_user_text = "确认删除 gone.txt"
    ok = await tools.delete_file("gone.txt", confirm=True)
    assert "已删除" in ok
    assert not target.exists()

    outside = await tools.delete_file(str(Path(__file__).resolve()), confirm=True)
    assert "工作区" in outside

    tools.last_user_text = "关机"
    power = await tools.power_action("shutdown", confirm=False)
    assert "确认" in power
    tools.last_user_text = "确认关机"
    still_power = await tools.power_action("shutdown", confirm=False)
    assert "确认" in still_power


@pytest.mark.asyncio
async def test_background_and_briefing_tools_register(tmp_path: Path) -> None:
    from config.settings import Settings
    from tools.builtin import BuiltinTools
    from tools.jobs import JobQueue
    from tools.registry import ToolRegistry

    settings = Settings(
        sqlite_path=str(tmp_path / "x.db"),
        workspace_root=str(tmp_path),
        deepseek_api_key="x",
    )
    jobs = JobQueue()
    registry = ToolRegistry()
    BuiltinTools(settings, jobs=jobs).register_all(registry)
    for name in (
        "daily_briefing",
        "background_task",
        "set_volume",
        "set_dnd",
        "lock_pc",
        "delete_file",
        "power_action",
    ):
        assert name in registry.names()
    queued = await registry.execute(
        {"id": "b", "name": "background_task", "arguments": {"instruction": "写一份摘要"}}
    )
    assert queued["ok"] is True
    assert "后台" in queued["output"]
    assert jobs.pop_nowait() == "写一份摘要"
