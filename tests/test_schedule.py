"""Calendar formatting and local reminders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory.reminders import format_reminder_list, parse_due
from senses.presence import STATUS_META, make_status_icon, status_label
from tools.calendar import format_agenda


def test_parse_due_delay_and_clock() -> None:
    now = datetime(2026, 9, 2, 14, 0, tzinfo=timezone(timedelta(hours=8)))
    due = parse_due(delay_minutes=10, now=now)
    assert due == datetime(2026, 9, 2, 6, 10, tzinfo=timezone.utc)
    later = parse_due(at_local="15:30", now=now)
    local = later.astimezone(now.tzinfo)
    assert local.hour == 15 and local.minute == 30
    tomorrow = parse_due(at_local="13:00", now=now)
    assert tomorrow.astimezone(now.tzinfo).day == 3


def test_format_agenda_and_reminders() -> None:
    text = format_agenda(
        [
            {
                "title": "周会",
                "start": "2026-09-02T07:00:00+00:00",
                "end": "2026-09-02T08:00:00+00:00",
                "location": "线上",
                "all_day": False,
            }
        ],
        [{"title": "交周报", "due": "2026-09-02T16:00:00+00:00"}],
        days=1,
    )
    assert "周会" in text
    assert "交周报" in text
    empty = format_agenda([], [], days=1)
    assert "没有事件" in empty
    assert "没有待办提醒" in format_reminder_list([])


def test_status_icon_colors() -> None:
    assert status_label("listening") == "聆听中"
    assert status_label("thinking") == "思考中"
    image = make_status_icon("idle")
    assert image.size == (64, 64)
    assert "idle" in STATUS_META


@pytest.mark.asyncio
async def test_reminder_store_roundtrip(tmp_path: Path) -> None:
    from config.settings import Settings
    from memory.manager import MemoryManager

    settings = Settings(sqlite_path=str(tmp_path / "r.db"), deepseek_api_key="x")
    memory = MemoryManager(settings, encode=lambda _t: __import__("numpy").zeros(8, dtype="float32"))
    await memory.initialize()
    assert memory.reminders is not None
    due = datetime.now(timezone.utc) - timedelta(seconds=1)
    item = await memory.reminders.add("喝水", due)
    claimed = await memory.reminders.claim_due()
    assert len(claimed) == 1
    assert claimed[0].id == item.id
    assert claimed[0].message == "喝水"
    leftover = await memory.reminders.list_pending()
    assert leftover == []
    await memory.close()


@pytest.mark.asyncio
async def test_schedule_tools_registered(tmp_path: Path) -> None:
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
    for name in ("calendar_agenda", "set_reminder", "list_reminders"):
        assert name in registry.names()
    missing = await registry.execute(
        {"id": "1", "name": "set_reminder", "arguments": {"message": "x", "delay_minutes": 1}}
    )
    assert missing["ok"] is True
    assert "未就绪" in missing["output"]
