"""Structured people / projects / stable preferences."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

KINDS = ("person", "project", "preference")
KIND_LABEL = {"person": "人物", "project": "项目", "preference": "偏好"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class EntityStore:
    """Named entities the assistant should keep straight across chats."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def upsert(self, kind: str, name: str, relation: str = "", notes: str = "") -> str:
        """Create or update one entity. kind is person/project/preference."""
        kind = (kind or "").strip().lower()
        if kind in {"人物", "人"}:
            kind = "person"
        if kind in {"项目"}:
            kind = "project"
        if kind in {"偏好", "习惯"}:
            kind = "preference"
        if kind not in KINDS:
            return "类型只能是人物、项目或偏好。"
        title = (name or "").strip()
        if not title:
            return "没有名字。"
        now = _utc_now()
        await self._db.execute(
            """
            INSERT INTO entities (kind, name, relation, notes, forgotten, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(kind, name) DO UPDATE SET
                relation = excluded.relation,
                notes = excluded.notes,
                forgotten = 0,
                updated_at = excluded.updated_at
            """,
            (kind, title, (relation or "").strip(), (notes or "").strip(), now, now),
        )
        await self._db.commit()
        return f"已记下{KIND_LABEL[kind]}「{title}」" + (f"（{relation}）" if relation else "") + "。"

    async def list_active(self, kind: str = "") -> list[dict[str, Any]]:
        """Return entities that have not been forgotten."""
        sql = """
            SELECT id, kind, name, relation, notes
            FROM entities
            WHERE forgotten = 0
        """
        params: list[Any] = []
        wanted = (kind or "").strip().lower()
        if wanted in {"人物", "人"}:
            wanted = "person"
        if wanted in {"项目"}:
            wanted = "project"
        if wanted in {"偏好", "习惯"}:
            wanted = "preference"
        if wanted in KINDS:
            sql += " AND kind = ?"
            params.append(wanted)
        sql += " ORDER BY kind, id"
        cursor = await self._db.execute(sql, params)
        return [dict(row) for row in await cursor.fetchall()]

    async def forget(self, name: str, kind: str = "") -> str:
        """Soft-delete entities whose name matches."""
        title = (name or "").strip()
        if not title:
            return "没有指定要忘掉的名字。"
        sql = "UPDATE entities SET forgotten = 1, updated_at = ? WHERE forgotten = 0 AND name = ?"
        params: list[Any] = [_utc_now(), title]
        wanted = (kind or "").strip().lower()
        if wanted in KINDS:
            sql += " AND kind = ?"
            params.append(wanted)
        cursor = await self._db.execute(sql, params)
        await self._db.commit()
        if cursor.rowcount < 1:
            return f"没有找到还记得的「{title}」。"
        return f"已忘掉「{title}」。"

    def format_list(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "还没有记下人物、项目或偏好。"
        lines = []
        for row in rows:
            label = KIND_LABEL.get(str(row["kind"]), row["kind"])
            extra = f"（{row['relation']}）" if row.get("relation") else ""
            notes = f"：{row['notes']}" if row.get("notes") else ""
            lines.append(f"- {label} {row['name']}{extra}{notes}")
        return "人物 / 项目 / 偏好：\n" + "\n".join(lines)

    def as_prompt(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        return self.format_list(rows)
