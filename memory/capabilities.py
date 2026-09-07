"""Saved capability paths: what we cannot do yet, and which source the user picked."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

STATUSES = ("chosen", "waiting_key", "ready")
STATUS_LABEL = {
    "chosen": "已选定",
    "waiting_key": "等Key",
    "ready": "可用",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_status(status: str) -> str:
    text = (status or "").strip().lower()
    aliases = {
        "chosen": "chosen",
        "selected": "chosen",
        "已选定": "chosen",
        "选定": "chosen",
        "waiting_key": "waiting_key",
        "waiting": "waiting_key",
        "等key": "waiting_key",
        "等密钥": "waiting_key",
        "ready": "ready",
        "可用": "ready",
        "已接上": "ready",
    }
    return aliases.get(text, "") or aliases.get(status.strip(), "")


class CapabilityStore:
    """Named capability paths the assistant should reuse instead of starting from zero."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def upsert(
        self,
        name: str,
        source: str,
        *,
        status: str = "waiting_key",
        homepage: str = "",
        notes: str = "",
        env_key: str = "",
    ) -> str:
        title = (name or "").strip()
        if not title:
            return "没有能力名称。"
        wanted = normalize_status(status) or "waiting_key"
        if wanted not in STATUSES:
            return "状态只能是已选定、等Key、可用。"
        now = _utc_now()
        await self._db.execute(
            """
            INSERT INTO capabilities (name, source, status, homepage, notes, env_key, forgotten, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                source = excluded.source,
                status = excluded.status,
                homepage = excluded.homepage,
                notes = excluded.notes,
                env_key = excluded.env_key,
                forgotten = 0,
                updated_at = excluded.updated_at
            """,
            (
                title,
                (source or "").strip(),
                wanted,
                (homepage or "").strip(),
                (notes or "").strip(),
                (env_key or "").strip().upper(),
                now,
                now,
            ),
        )
        await self._db.commit()
        label = STATUS_LABEL[wanted]
        return f"已记下能力「{title}」：{source or '未指定源'}（{label}）。未确认不装包、不改程序。"

    async def list_active(self) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
            """
            SELECT id, name, source, status, homepage, notes, env_key
            FROM capabilities
            WHERE forgotten = 0
            ORDER BY id
            """
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def find_matching(self, need: str) -> list[dict[str, Any]]:
        """Return saved rows whose name or notes overlap the current need."""
        query = (need or "").strip()
        rows = await self.list_active()
        if not query:
            return rows
        hits = []
        for row in rows:
            blob = f"{row.get('name') or ''} {row.get('notes') or ''} {row.get('source') or ''}"
            if any(token and token in blob for token in _need_tokens(query)):
                hits.append(row)
        return hits or []

    async def forget(self, name: str) -> str:
        title = (name or "").strip()
        if not title:
            return "没有指定要忘掉的能力。"
        cursor = await self._db.execute(
            "UPDATE capabilities SET forgotten = 1, updated_at = ? WHERE forgotten = 0 AND name = ?",
            (_utc_now(), title),
        )
        await self._db.commit()
        if cursor.rowcount < 1:
            return f"没有找到还记得的能力「{title}」。"
        return f"已忘掉能力「{title}」。"

    def format_list(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "还没有铺过的能力路。"
        lines = ["已铺的能力："]
        for row in rows:
            label = STATUS_LABEL.get(str(row.get("status") or ""), row.get("status"))
            home = f" {row['homepage']}" if row.get("homepage") else ""
            extra = f"；环境变量 {row['env_key']}" if row.get("env_key") else ""
            notes = f"；{row['notes']}" if row.get("notes") else ""
            lines.append(f"- {row['name']} → {row.get('source') or '未指定'}（{label}）{home}{extra}{notes}")
        return "\n".join(lines)

    def as_prompt(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        return self.format_list(rows)


def _need_tokens(need: str) -> list[str]:
    text = need.strip()
    tokens = [text]
    for piece in ("行情", "收盘", "成交", "日线", "K线", "k线", "股票", "API", "接口"):
        if piece in text:
            tokens.append(piece)
    return tokens
