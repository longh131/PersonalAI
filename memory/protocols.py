"""Speak/silence protocols: quiet hours, meeting mute, master mute."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

VALID_KEYS = ("mute", "meeting_mute", "quiet_hours")
KEY_LABEL = {
    "mute": "静音（什么都不说）",
    "meeting_mute": "会议中不出声",
    "quiet_hours": "夜间只提醒",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_hhmm(text: str) -> int | None:
    """Return minutes from midnight, or None if invalid."""
    parts = (text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def in_quiet_hours(start: str, end: str, *, now: datetime | None = None) -> bool:
    """True when local time is inside [start, end), including overnight spans."""
    start_m = parse_hhmm(start)
    end_m = parse_hhmm(end)
    if start_m is None or end_m is None:
        return False
    stamp = now or datetime.now().astimezone()
    current = stamp.hour * 60 + stamp.minute
    if start_m == end_m:
        return False
    if start_m < end_m:
        return start_m <= current < end_m
    return current >= start_m or current < end_m


class ProtocolStore:
    """SQLite-backed protocol flags the standby loop consults before TTS."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def set(self, key: str, enabled: bool, config: dict[str, Any] | None = None) -> str:
        """Enable or disable a named protocol."""
        name = _normalize_key(key)
        if name is None:
            return "协议只能是：静音、会议中不出声、夜间只提醒。"
        now = _utc_now()
        payload = dict(config or {})
        if name == "quiet_hours":
            payload.setdefault("start", "22:00")
            payload.setdefault("end", "08:00")
        await self._db.execute(
            """
            INSERT INTO protocols (key, enabled, config_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (name, 1 if enabled else 0, json.dumps(payload, ensure_ascii=False), now),
        )
        await self._db.commit()
        label = KEY_LABEL[name]
        if not enabled:
            return f"已关闭：{label}。"
        extra = ""
        if name == "quiet_hours":
            extra = f"（{payload.get('start')}–{payload.get('end')}）"
        return f"已开启：{label}{extra}。"

    async def list_all(self) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
            "SELECT key, enabled, config_json FROM protocols ORDER BY key"
        )
        rows = []
        for row in await cursor.fetchall():
            item = dict(row)
            try:
                item["config"] = json.loads(item.get("config_json") or "{}")
            except json.JSONDecodeError:
                item["config"] = {}
            rows.append(item)
        return rows

    def format_list(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "没有开启的协议。可以说「会议中不要出声」或「晚上十点后只提醒」。"
        lines = []
        for row in rows:
            label = KEY_LABEL.get(str(row["key"]), row["key"])
            state = "开" if int(row.get("enabled") or 0) else "关"
            cfg = row.get("config") or {}
            extra = ""
            if row["key"] == "quiet_hours" and cfg:
                extra = f" {cfg.get('start', '')}–{cfg.get('end', '')}"
            lines.append(f"- {label}：{state}{extra}")
        return "协议：\n" + "\n".join(lines)

    async def may_speak(self, kind: str, *, meeting: bool = False) -> tuple[bool, str]:
        """Whether TTS is allowed. kind is reply / reminder / alert / briefing / job."""
        rows = {row["key"]: row for row in await self.list_all()}
        mute = rows.get("mute")
        if mute and int(mute.get("enabled") or 0):
            return False, "静音协议"
        meeting_row = rows.get("meeting_mute")
        if meeting and meeting_row and int(meeting_row.get("enabled") or 0):
            return False, "会议中不出声"
        quiet = rows.get("quiet_hours")
        if quiet and int(quiet.get("enabled") or 0):
            cfg = quiet.get("config") or {}
            if in_quiet_hours(str(cfg.get("start") or "22:00"), str(cfg.get("end") or "08:00")):
                if kind not in {"reminder", "briefing", "job"}:
                    return False, "夜间只提醒"
        return True, ""


def _normalize_key(key: str) -> str | None:
    text = (key or "").strip().lower()
    aliases = {
        "mute": "mute",
        "静音": "mute",
        "不要出声": "mute",
        "免打扰": "mute",
        "meeting_mute": "meeting_mute",
        "会议": "meeting_mute",
        "会议中不出声": "meeting_mute",
        "quiet_hours": "quiet_hours",
        "夜间": "quiet_hours",
        "夜间只提醒": "quiet_hours",
        "免打扰时段": "quiet_hours",
    }
    return aliases.get(text)


def looks_like_meeting(title: str, exe: str = "") -> bool:
    """True when the foreground app looks like a video meeting."""
    blob = f"{title} {exe}".lower()
    markers = (
        "zoom",
        "teams",
        "webex",
        "tencent",
        "voov",
        "wemeet",
        "腾讯会议",
        "钉钉",
        "dingtalk",
        "feishu",
        "lark",
        "飞书",
        "google meet",
        "meet.google",
        "slack",
    )
    return any(marker in blob for marker in markers)
