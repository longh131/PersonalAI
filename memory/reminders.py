"""Persistent local reminders (due time + message)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import re

import aiosqlite


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_due(
    *,
    delay_minutes: float | None = None,
    delay_seconds: float | None = None,
    at_local: str | None = None,
    now: datetime | None = None,
) -> datetime:
    """Resolve a due time in the local timezone, returned as UTC-aware datetime."""
    local_now = now or datetime.now().astimezone()
    if delay_seconds is not None and str(delay_seconds) != "":
        due_local = local_now + timedelta(seconds=float(delay_seconds))
        return due_local.astimezone(timezone.utc)
    if delay_minutes is not None and str(delay_minutes) != "":
        due_local = local_now + timedelta(minutes=float(delay_minutes))
        return due_local.astimezone(timezone.utc)
    text = (at_local or "").strip()
    if not text:
        raise ValueError("需要 delay_minutes、delay_seconds 或 at_local")
    clock = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(f"无效时刻：{text}")
        due_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due_local <= local_now:
            due_local += timedelta(days=1)
        return due_local.astimezone(timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(text, fmt)
            due_local = naive.replace(tzinfo=local_now.tzinfo)
            return due_local.astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间：{text}")


@dataclass(slots=True)
class Reminder:
    """One scheduled local reminder."""

    id: int
    message: str
    due_at: str
    status: str

    def due_local_text(self) -> str:
        """Format due_at (UTC ISO) as local wall time."""
        raw = self.due_at
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone().strftime("%m-%d %H:%M")
        except ValueError:
            return raw


class ReminderStore:
    """SQLite-backed reminder CRUD on the shared memory database."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def add(self, message: str, due_utc: datetime) -> Reminder:
        """Insert a pending reminder and return it with id."""
        text = (message or "").strip()
        if not text:
            raise ValueError("提醒内容不能为空")
        due_iso = due_utc.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        now = _utc_now().isoformat()
        cursor = await self._db.execute(
            """
            INSERT INTO reminders (message, due_at, status, created_at)
            VALUES (?, ?, 'pending', ?)
            """,
            (text, due_iso, now),
        )
        await self._db.commit()
        return Reminder(id=int(cursor.lastrowid or 0), message=text, due_at=due_iso, status="pending")

    async def list_pending(self) -> list[Reminder]:
        """Return pending reminders, soonest first."""
        cursor = await self._db.execute(
            """
            SELECT id, message, due_at, status
            FROM reminders
            WHERE status = 'pending'
            ORDER BY due_at ASC
            LIMIT 30
            """
        )
        rows = await cursor.fetchall()
        return [_row_to_reminder(row) for row in rows]

    async def cancel(self, reminder_id: int) -> bool:
        """Mark a pending reminder cancelled. Returns False if not found."""
        cursor = await self._db.execute(
            """
            UPDATE reminders SET status = 'cancelled'
            WHERE id = ? AND status = 'pending'
            """,
            (int(reminder_id),),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def claim_due(self, now: datetime | None = None) -> list[Reminder]:
        """Atomically take all pending reminders whose due time has passed."""
        stamp = (now or _utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat()
        cursor = await self._db.execute(
            """
            SELECT id, message, due_at, status
            FROM reminders
            WHERE status = 'pending' AND due_at <= ?
            ORDER BY due_at ASC
            """,
            (stamp,),
        )
        rows = await cursor.fetchall()
        items = [_row_to_reminder(row) for row in rows]
        if not items:
            return []
        ids = [item.id for item in items]
        placeholders = ",".join("?" * len(ids))
        fired_at = _utc_now().isoformat()
        await self._db.execute(
            f"UPDATE reminders SET status = 'fired', fired_at = ? WHERE id IN ({placeholders})",
            (fired_at, *ids),
        )
        await self._db.commit()
        for item in items:
            item.status = "fired"
        return items


def _row_to_reminder(row: Any) -> Reminder:
    mapping = dict(row)
    return Reminder(
        id=int(mapping["id"]),
        message=str(mapping["message"]),
        due_at=str(mapping["due_at"]),
        status=str(mapping["status"]),
    )


def format_reminder_list(items: list[Reminder]) -> str:
    """Human-readable pending reminder list."""
    if not items:
        return "没有待办提醒。"
    lines = [f"{item.id}. {item.due_local_text()}  {item.message}" for item in items]
    return "待办提醒：\n" + "\n".join(lines)
