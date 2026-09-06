"""Short morning / leaving briefings from calendar, reminders, and ship status."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from memory.reminders import Reminder, format_reminder_list
from tools.calendar import fetch_outlook_agenda
from tools.health import format_status, read_system_snapshot


def auto_briefing_kind(now: datetime | None = None) -> str | None:
    """Pick morning (05–12) or leaving (17–23). None means don't auto-brief."""
    stamp = now or datetime.now().astimezone()
    hour = stamp.hour
    if 5 <= hour < 12:
        return "morning"
    if 17 <= hour < 23:
        return "leaving"
    return None


def _trim(text: str, max_lines: int = 4) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + " …"


async def compose_briefing(
    kind: str,
    *,
    reminders: list[Reminder] | None = None,
    agenda: str | None = None,
    status: str | None = None,
) -> str:
    """Build a <= few-line briefing. Outlook is optional and time-capped by the caller."""
    heading = "离开简报" if kind == "leaving" else "晨间简报"
    days = 2 if kind == "leaving" else 1
    if agenda is None:
        try:
            agenda = await asyncio.wait_for(fetch_outlook_agenda(days=days, include_tasks=True), timeout=8)
        except Exception:  # noqa: BLE001
            agenda = "日程暂时读不到。"
    if status is None:
        try:
            status = format_status(read_system_snapshot())
        except Exception:  # noqa: BLE001
            status = ""
    reminder_text = format_reminder_list(reminders or [])
    parts = [f"{heading}："]
    if agenda:
        parts.append(_trim(agenda, 4))
    if reminder_text and "没有待办提醒" not in reminder_text:
        parts.append(_trim(reminder_text, 3))
    extra = _anomaly_status(status or "")
    if extra:
        parts.append(extra)
    text = "\n".join(parts)
    if len(text) > 400:
        text = text[:400] + "…"
    return text


def _anomaly_status(status: str) -> str:
    """Keep only ship-status problems: unplugged battery, offline, disk under 5 GB."""
    bits: list[str] = []
    if "不通" in status:
        bits.append("网络不通")
    bat_line = next(
        (line.strip() for line in status.splitlines() if line.startswith("电量") and "未插电" in line),
        "",
    )
    if bat_line:
        bits.append(bat_line)
    disk_line = next((line.strip() for line in status.splitlines() if "剩余" in line), "")
    if disk_line:
        match = re.search(r"剩余\s+([\d.]+)\s*GB", disk_line)
        if match and float(match.group(1)) < 5:
            bits.append(disk_line)
    return "；".join(bits)
