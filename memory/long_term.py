"""Long-term structured memories with vector retrieval."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite
import numpy as np
from loguru import logger


def _tokens(query: str) -> list[str]:
    """Split a query into CJK bigrams/words and latin tokens for LIKE fallback."""
    found = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", query or "")
    unique: list[str] = []
    for token in found:
        if token not in unique:
            unique.append(token)
    return unique[:6]


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class LongTermMemory:
    """Durable facts, preferences, events, relationships and knowledge."""

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
        content: str,
        kind: str,
        importance: float,
        metadata: dict[str, Any] | None = None,
        summary: str | None = None,
        source: str = "dialogue",
    ) -> int:
        """Insert a long-term memory and its embedding.

        Args:
            content: Canonical text to store.
            kind: One of fact/preference/event/relationship/knowledge.
            importance: 0-1 score used by retrieval ranking.
            metadata: Optional JSON-serializable extras.
            summary: Optional shorter form; defaults to `content`.
            source: Provenance tag.

        Returns:
            New row id.
        """
        now = _utc_now()
        vector = self._encode(content)
        cursor = await self._db.execute(
            """
            INSERT INTO memories (
                kind, content, summary, importance, source, embedding, embedding_model,
                access_count, last_accessed, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                kind,
                content,
                summary or content,
                float(importance),
                source,
                vector.astype(np.float32).tobytes(),
                self._model_name,
                now,
                now,
                now,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        await self._db.commit()
        memory_id = int(cursor.lastrowid)
        logger.info("Stored long-term memory id={} kind={}", memory_id, kind)
        return memory_id

    async def search(self, query_vector: np.ndarray, k: int = 5) -> list[dict[str, Any]]:
        """Return the top-k memories by cosine similarity.

        Args:
            query_vector: Embedded query (preferably L2-normalized).
            k: Maximum hits to return.
        """
        cursor = await self._db.execute(
            "SELECT id, kind, content, summary, importance, metadata_json, embedding FROM memories WHERE embedding IS NOT NULL"
        )
        rows = await cursor.fetchall()
        scored = _score_rows(rows, query_vector)
        hits = scored[:k]
        if hits:
            ids = [item["id"] for item in hits]
            now = _utc_now()
            for memory_id in ids:
                await self._db.execute(
                    "UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                    (now, memory_id),
                )
            await self._db.commit()
        return hits

    async def keyword_search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Substring fallback used when embeddings are weak or hash-based."""
        tokens = _tokens(query)
        if not tokens:
            return []
        clauses = " OR ".join(["content LIKE ? OR summary LIKE ?"] * len(tokens))
        params: list[Any] = []
        for token in tokens:
            wildcard = f"%{token}%"
            params.extend([wildcard, wildcard])
        params.append(k)
        cursor = await self._db.execute(
            f"""
            SELECT id, kind, content, summary, importance, metadata_json
            FROM memories
            WHERE {clauses}
            ORDER BY importance DESC, id DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["score"] = 0.55
            results.append(item)
        return results

    async def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        """Return newest memories without vector search (used for user-model overlay)."""
        cursor = await self._db.execute(
            """
            SELECT id, kind, content, summary, importance, metadata_json
            FROM memories
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("embedding", None)
            item["score"] = 1.0
            results.append(item)
        return results


def _score_rows(rows: list[aiosqlite.Row], query_vector: np.ndarray) -> list[dict[str, Any]]:
    """Rank SQLite rows that contain an embedding blob."""
    query = np.asarray(query_vector, dtype=np.float32)
    norm = float(np.linalg.norm(query)) + 1e-8
    query = query / norm
    scored: list[dict[str, Any]] = []
    for row in rows:
        blob = row["embedding"]
        if not blob:
            continue
        vector = np.frombuffer(blob, dtype=np.float32)
        if vector.size == 0:
            continue
        vector = vector / (float(np.linalg.norm(vector)) + 1e-8)
        score = float(np.dot(query, vector))
        item = {key: row[key] for key in row.keys() if key != "embedding"}
        item["score"] = score
        scored.append(item)
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored
