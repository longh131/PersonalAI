"""Experience memory: successful and failed action trajectories."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite
import numpy as np

from memory.long_term import _score_rows


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ExperienceMemory:
    """Stores lessons from tool-using tasks so similar work can be improved."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        encode: Callable[[str], np.ndarray],
        model_name: str,
    ) -> None:
        self._db = db
        self._encode = encode
        self._model_name = model_name

    async def add(
        self,
        task_summary: str,
        actions: list[dict[str, Any]],
        outcome: str,
        lesson: str,
    ) -> int:
        """Persist one experience episode.

        Args:
            task_summary: What the user asked to accomplish.
            actions: Sequence of tool invocations and results.
            outcome: success | failure | partial.
            lesson: What to repeat or avoid next time.

        Returns:
            New row id.
        """
        text = f"{task_summary}\n{lesson}"
        vector = self._encode(text)
        cursor = await self._db.execute(
            """
            INSERT INTO experiences (
                task_summary, actions_json, outcome, lesson, embedding, embedding_model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_summary,
                json.dumps(actions, ensure_ascii=False),
                outcome,
                lesson,
                vector.astype(np.float32).tobytes(),
                self._model_name,
                _utc_now(),
            ),
        )
        await self._db.commit()
        return int(cursor.lastrowid)

    async def search(self, query_vector: np.ndarray, k: int = 3) -> list[dict[str, Any]]:
        """Return similar past experiences by embedding cosine similarity."""
        cursor = await self._db.execute(
            """
            SELECT id, task_summary, actions_json, outcome, lesson, embedding
            FROM experiences
            WHERE embedding IS NOT NULL
            """
        )
        rows = await cursor.fetchall()
        hits = _score_rows(rows, query_vector)[:k]
        for hit in hits:
            hit["content"] = f"[{hit.get('outcome')}] {hit.get('task_summary')} → {hit.get('lesson')}"
        return hits
