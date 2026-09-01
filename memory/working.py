"""Working memory: the active task / goal / step for the current session."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class WorkingMemory:
    """Persisted task state used while a multi-step job is in flight."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def get_active(self, session_id: str) -> dict[str, Any] | None:
        """Return the newest in-progress or pending task for a session.

        Args:
            session_id: Current engine session identifier.

        Returns:
            Task mapping or `None` when no active task exists.
        """
        cursor = await self._db.execute(
            """
            SELECT id, session_id, title, goal, status, current_step, context_json,
                   created_at, updated_at
            FROM tasks
            WHERE session_id = ? AND status IN ('pending', 'in_progress')
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def save(self, session_id: str, payload: dict[str, Any]) -> int:
        """Insert or update a working task.

        Args:
            session_id: Current engine session identifier.
            payload: Task fields. May include `id` for updates.

        Returns:
            The task primary key.
        """
        now = _utc_now()
        context = payload.get("context_json") or payload.get("context") or {}
        if not isinstance(context, str):
            context = json.dumps(context, ensure_ascii=False)
        task_id = payload.get("id")
        title = str(payload.get("title") or "untitled")
        goal = str(payload.get("goal") or "")
        status = str(payload.get("status") or "in_progress")
        current_step = payload.get("current_step")
        if task_id:
            await self._db.execute(
                """
                UPDATE tasks
                SET title = ?, goal = ?, status = ?, current_step = ?,
                    context_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, goal, status, current_step, context, now, int(task_id)),
            )
            await self._db.commit()
            return int(task_id)
        cursor = await self._db.execute(
            """
            INSERT INTO tasks (session_id, title, goal, status, current_step, context_json,
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, title, goal, status, current_step, context, now, now),
        )
        await self._db.commit()
        return int(cursor.lastrowid)

    def snapshot(self, task: dict[str, Any] | None) -> dict[str, Any]:
        """Return a compact snapshot suitable for LangGraph state.

        Args:
            task: Full task row or `None`.
        """
        if not task:
            return {}
        return {
            "id": task.get("id"),
            "title": task.get("title"),
            "goal": task.get("goal"),
            "status": task.get("status"),
            "current_step": task.get("current_step"),
        }

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """Convert a SQLite row into a plain dictionary."""
        data = dict(row)
        raw = data.get("context_json") or "{}"
        try:
            data["context"] = json.loads(raw)
        except json.JSONDecodeError:
            data["context"] = {}
        return data
