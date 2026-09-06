"""Ship status, forgettable memory, entities, and speak protocols."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory.protocols import in_quiet_hours, looks_like_meeting
from tools.health import HealthWatch, SystemSnapshot, format_status


def test_quiet_hours_overnight() -> None:
    noon = datetime(2026, 9, 2, 12, 0)
    night = datetime(2026, 9, 2, 23, 0)
    dawn = datetime(2026, 9, 3, 7, 0)
    assert in_quiet_hours("22:00", "08:00", now=night) is True
    assert in_quiet_hours("22:00", "08:00", now=dawn) is True
    assert in_quiet_hours("22:00", "08:00", now=noon) is False


def test_meeting_window_hints() -> None:
    assert looks_like_meeting("Zoom Meeting", "Zoom.exe") is True
    assert looks_like_meeting("腾讯会议", "wemeetapp.exe") is True
    assert looks_like_meeting("Cursor", "Cursor.exe") is False


def test_health_alerts_cooldown() -> None:
    watch = HealthWatch(cooldown_s=100)
    snap = SystemSnapshot(battery=8, plugged=False, disk_free_gb=80, disk_path="C:\\", online=True)
    first = watch.alerts(snap, now=1.0)
    assert any("电量" in item for item in first)
    again = watch.alerts(snap, now=2.0)
    assert again == []
    later = watch.alerts(snap, now=200.0)
    assert any("电量" in item for item in later)
    text = format_status(snap)
    assert "8%" in text


@pytest.mark.asyncio
async def test_forget_and_entity_and_protocol(tmp_path: Path) -> None:
    from config.settings import Settings
    from memory.manager import MemoryManager, hash_embed

    settings = Settings(sqlite_path=str(tmp_path / "m.db"), deepseek_api_key="x")
    memory = MemoryManager(settings, encode=hash_embed)
    await memory.initialize()
    assert memory.long_term and memory.entities and memory.protocols
    row_id = await memory.long_term.add("去青岛的行程", "event", 0.8)
    forgotten = await memory.forget_memories(memory_id=row_id)
    assert "已忘掉" in forgotten
    hits = await memory.search("青岛行程", k=5)
    assert not any("青岛" in hit.content for hit in hits)

    note = await memory.entities.upsert("person", "张三", relation="老板")
    assert "张三" in note
    listed = memory.entities.format_list(await memory.entities.list_active())
    assert "老板" in listed
    await memory.entities.forget("张三")
    empty = await memory.entities.list_active()
    assert empty == []

    opened = await memory.protocols.set("meeting_mute", True)
    assert "会议" in opened
    allowed, reason = await memory.protocols.may_speak("reply", meeting=True)
    assert allowed is False
    assert reason
    quiet_ok, _ = await memory.protocols.may_speak("reminder", meeting=False)
    assert quiet_ok is True
    await memory.kv_set("brief:morning:2026-09-02", "1")
    assert await memory.kv_get("brief:morning:2026-09-02") == "1"
    assert await memory.kv_get("missing") is None
    await memory.close()
