"""Self memory: the assistant's evolving self-knowledge."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite
import numpy as np

from memory.long_term import _score_rows


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SelfMemory:
    """Key-value self model that is also semantically searchable."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        encode: Callable[[str], np.ndarray],
        model_name: str,
    ) -> None:
        self._db = db
        self._encode = encode
        self._model_name = model_name

    async def get(self, key: str) -> str | None:
        """Return the content for a self-memory key, if present."""
        cursor = await self._db.execute(
            "SELECT content FROM self_memory WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        return str(row["content"]) if row else None

    async def upsert(self, key: str, content: str) -> None:
        """Insert or update a self-memory entry and refresh its embedding.

        Args:
            key: Stable identifier such as `capabilities` or `user_corrections`.
            content: Free-text self knowledge.
        """
        vector = self._encode(f"{key}: {content}").astype(np.float32).tobytes()
        now = _utc_now()
        cursor = await self._db.execute(
            "SELECT id, version FROM self_memory WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        if row:
            await self._db.execute(
                """
                UPDATE self_memory
                SET content = ?, version = version + 1, embedding = ?, embedding_model = ?, updated_at = ?
                WHERE key = ?
                """,
                (content, vector, self._model_name, now, key),
            )
        else:
            await self._db.execute(
                """
                INSERT INTO self_memory (key, content, version, embedding, embedding_model, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (key, content, vector, self._model_name, now),
            )
        await self._db.commit()

    async def all_text(self) -> str:
        """Render all self-memory entries for identity injection."""
        cursor = await self._db.execute(
            "SELECT key, content FROM self_memory ORDER BY key"
        )
        rows = await cursor.fetchall()
        if not rows:
            return ""
        return "\n".join(f"- {row['key']}: {row['content']}" for row in rows)

    async def search(self, query_vector: np.ndarray, k: int = 3) -> list[dict[str, Any]]:
        """Semantically search self-memory entries."""
        cursor = await self._db.execute(
            "SELECT id, key, content, embedding FROM self_memory WHERE embedding IS NOT NULL"
        )
        rows = await cursor.fetchall()
        hits = _score_rows(rows, query_vector)[:k]
        for hit in hits:
            hit["content"] = f"{hit.get('key')}: {hit.get('content')}"
        return hits
