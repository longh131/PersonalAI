"""Memory manager: SQLite schema, embedder, weave, and intelligent persist."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite
import numpy as np
from loguru import logger

from config.settings import Settings
from memory.experience import ExperienceMemory
from memory.long_term import LongTermMemory
from memory.self_memory import SelfMemory
from memory.short_term import ShortTermMemory
from memory.working import WorkingMemory

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL CHECK(kind IN ('fact','preference','event','relationship','knowledge')),
    content         TEXT NOT NULL,
    summary         TEXT NOT NULL,
    importance      REAL NOT NULL DEFAULT 0.5 CHECK(importance BETWEEN 0 AND 1),
    source          TEXT NOT NULL DEFAULT 'dialogue',
    embedding       BLOB,
    embedding_model TEXT,
    access_count    INTEGER NOT NULL DEFAULT 0,
    last_accessed   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT,
    title          TEXT NOT NULL,
    goal           TEXT NOT NULL,
    status         TEXT NOT NULL CHECK(status IN ('pending','in_progress','completed','failed','paused')),
    current_step   TEXT,
    context_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS experiences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_summary    TEXT NOT NULL,
    actions_json    TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK(outcome IN ('success','failure','partial')),
    lesson          TEXT NOT NULL,
    embedding       BLOB,
    embedding_model TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS self_memory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key             TEXT NOT NULL UNIQUE,
    content         TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    embedding       BLOB,
    embedding_model TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    yaml_text     TEXT NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL
);
"""

SKIP_PHRASES = ("你好", "您好", "在吗", "嗨", "hi", "hello", "hey", "早上好", "晚安")
FORCE_SAVE_MARKERS = ("记住", "记下", "别忘了", "记一下", "请记住")
CORRECTION_MARKERS = ("不对", "你错了", "纠正", "不是这样", "以后不要")


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_embed(text: str, dim: int = 384) -> np.ndarray:
    """Deterministic fallback embedding used in tests and when the model is unavailable.

    Args:
        text: Input string.
        dim: Output dimensionality.

    Returns:
        L2-normalized float32 vector.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "little", signed=False)
    rng = np.random.default_rng(seed)
    vector = rng.standard_normal(dim).astype(np.float32)
    norm = float(np.linalg.norm(vector)) + 1e-8
    return vector / norm


class TextEmbedder:
    """Sentence-transformer embedder with a hash fallback."""

    def __init__(self, model_name: str, dim: int = 384) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model: Any = None
        self.backend: str = "hash"

    def load(self) -> None:
        """Load cached sentence-transformer weights. Never wait on Hugging Face at boot.

        If the model is not already on disk, fall back to hash embeddings plus
        keyword search so startup does not hang on a Hub timeout.
        """
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, local_files_only=True)
            self.dim = int(self._model.get_sentence_embedding_dimension())
            self.backend = "sentence-transformers"
            logger.info("Embedding model ready: {} dim={}", self.model_name, self.dim)
        except Exception as exc:  # noqa: BLE001 - missing cache / Hub blocked
            self._model = None
            self.backend = "hash"
            logger.warning(
                "Embedding model not in local cache ({}). Using hash+keyword memory; "
                "Hugging Face is not contacted at startup.",
                exc,
            )

    def encode(self, text: str) -> np.ndarray:
        """Embed a single string into a normalized vector."""
        cleaned = (text or "").strip() or " "
        if self._model is None:
            return hash_embed(cleaned, self.dim)
        vector = self._model.encode(cleaned, normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32)


@dataclass(slots=True)
class MemoryHit:
    """A retrieved memory used by the context weaver."""

    id: int
    layer: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryContext:
    """Bundle produced for the weave_context node."""

    identity_overlay: str
    short_term_messages: list[dict[str, Any]]
    working_snapshot: dict[str, Any]
    long_term_hits: list[MemoryHit]
    experience_hits: list[MemoryHit]
    self_hits: list[MemoryHit]
    self_knowledge: str


@dataclass(slots=True)
class PersistDecision:
    """Whether a turn should enter long-term / experience / self memory."""

    should_save: bool
    kind: str | None
    summary: str
    importance: float
    reason: str
    target: str = "long_term"


class MemoryManager:
    """Single entry point for all five memory layers."""

    def __init__(
        self,
        settings: Settings,
        embedder: TextEmbedder | None = None,
        encode: Callable[[str], np.ndarray] | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder or TextEmbedder(settings.embedding_model)
        self._encode_override = encode
        self._db: aiosqlite.Connection | None = None
        self.short_term = ShortTermMemory(max_turns=settings.short_term_window)
        self.working: WorkingMemory | None = None
        self.long_term: LongTermMemory | None = None
        self.experience: ExperienceMemory | None = None
        self.self_memory: SelfMemory | None = None

    def encode(self, text: str) -> np.ndarray:
        """Embed text using the override or the configured embedder."""
        if self._encode_override is not None:
            return self._encode_override(text)
        return self.embedder.encode(text)

    async def initialize(self) -> None:
        """Open SQLite, apply schema, load embedder, and seed self memory."""
        if self._db is not None:
            return
        path = self.settings.resolve_sqlite_path()
        self._db = await aiosqlite.connect(str(path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        if self._encode_override is None:
            self.embedder.load()
        encode = self.encode
        model_name = self.embedder.model_name
        self.working = WorkingMemory(self._db)
        self.long_term = LongTermMemory(self._db, encode, model_name)
        self.experience = ExperienceMemory(self._db, encode, model_name)
        self.self_memory = SelfMemory(self._db, encode, model_name)
        if await self.self_memory.get("capabilities") is None:
            await self.self_memory.upsert(
                "capabilities",
                "文本对话、语音听写与播报、截屏/图片 OCR 与理解、文件读取与搜索、网页搜索、受限代码执行、选择性长期记忆。",
            )
        if await self.self_memory.get("limits") is None:
            await self.self_memory.upsert(
                "limits",
                "没有独立躯体；不能在未授权系统上提权；语音和视觉依赖本机麦克风、扬声器和模型文件。",
            )
        logger.info("Memory store ready at {}", path)

    async def ensure_session(self, session_id: str) -> None:
        """Create the session row if it does not exist."""
        assert self._db is not None
        now = _utc_now()
        await self._db.execute(
            """
            INSERT INTO sessions (id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (session_id, now, now),
        )
        await self._db.commit()

    async def weave(self, query: str, session_id: str) -> MemoryContext:
        """Retrieve a cross-layer context bundle for the current user query."""
        assert self.working and self.long_term and self.experience and self.self_memory
        vector = self.encode(query)
        long_hits = await self.long_term.search(vector, k=5)
        if not long_hits or float(long_hits[0].get("score") or 0) < 0.45:
            extra = await self.long_term.keyword_search(query, k=5)
            seen = {item.get("id") for item in long_hits}
            long_hits = long_hits + [item for item in extra if item.get("id") not in seen]
        exp_hits = await self.experience.search(vector, k=3)
        self_hits = await self.self_memory.search(vector, k=3)
        active = await self.working.get_active(session_id)
        user_bits = [
            item["content"]
            for item in long_hits
            if item.get("kind") in {"fact", "preference", "relationship"}
        ]
        return MemoryContext(
            identity_overlay="\n".join(f"- {bit}" for bit in user_bits),
            short_term_messages=self.short_term.window(),
            working_snapshot=self.working.snapshot(active),
            long_term_hits=[_as_hit(item, "long_term") for item in long_hits],
            experience_hits=[_as_hit(item, "experience") for item in exp_hits],
            self_hits=[_as_hit(item, "self") for item in self_hits],
            self_knowledge=await self.self_memory.all_text(),
        )

    async def remember_turn(self, session_id: str, role: str, content: str) -> None:
        """Append a turn to short-term memory and the conversation log."""
        assert self._db is not None
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        self.short_term.append({"role": role, "content": text})
        await self._db.execute(
            """
            INSERT INTO conversation_turns (session_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, text, _utc_now()),
        )
        await self._db.commit()

    async def search(self, query: str, *, k: int = 5) -> list[MemoryHit]:
        """Public semantic search across long-term, experience and self memory."""
        assert self.long_term and self.experience and self.self_memory
        vector = self.encode(query)
        long_hits = [_as_hit(item, "long_term") for item in await self.long_term.search(vector, k=k)]
        if not long_hits or long_hits[0].score < 0.45:
            extra = [_as_hit(item, "long_term") for item in await self.long_term.keyword_search(query, k=k)]
            seen = {hit.id for hit in long_hits}
            for hit in extra:
                if hit.id not in seen:
                    long_hits.append(hit)
        exp_hits = [_as_hit(item, "experience") for item in await self.experience.search(vector, k=3)]
        self_hits = [_as_hit(item, "self") for item in await self.self_memory.search(vector, k=3)]
        merged = long_hits + exp_hits + self_hits
        merged.sort(key=lambda hit: hit.score, reverse=True)
        return merged[:k]

    def heuristic_decision(self, user_text: str, assistant_text: str) -> PersistDecision:
        """Cheap persist gate used before (or instead of) an LLM judgment.

        Args:
            user_text: Latest user utterance.
            assistant_text: Latest assistant reply.

        Returns:
            A persist decision. `should_save` is False for greetings and trivia.
        """
        text = (user_text or "").strip()
        lowered = text.lower()
        if not text:
            return PersistDecision(False, None, "", 0.0, "empty")
        if len(text) <= 8 and any(text.startswith(p) or lowered == p for p in SKIP_PHRASES):
            return PersistDecision(False, None, "", 0.0, "greeting")
        if any(marker in text for marker in FORCE_SAVE_MARKERS):
            return PersistDecision(True, "fact", text, 0.9, "user asked to remember", "long_term")
        if any(marker in text for marker in CORRECTION_MARKERS):
            return PersistDecision(True, "knowledge", f"用户纠正：{text}", 0.85, "user correction", "self")
        if "我叫" in text or "我是" in text[:12]:
            return PersistDecision(True, "fact", text, 0.8, "self-identification", "long_term")
        if "我喜欢" in text or "我不喜欢" in text or "以后请" in text:
            return PersistDecision(True, "preference", text, 0.75, "preference", "long_term")
        if len(text) < 12:
            return PersistDecision(False, None, "", 0.1, "too short")
        return PersistDecision(False, "fact", assistant_text[:120], 0.4, "needs model judgment")

    async def maybe_persist(
        self,
        user_text: str,
        assistant_text: str,
        decision: PersistDecision,
        *,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> int | None:
        """Write a memory only when the decision says so.

        Returns:
            Inserted row id, or `None` when nothing was stored.
        """
        assert self.long_term and self.experience and self.self_memory
        if not decision.should_save:
            logger.debug("Skip persist: {}", decision.reason)
            return None
        target = decision.target or "long_term"
        if target == "self":
            previous = await self.self_memory.get("user_corrections") or ""
            merged = (previous + "\n" + decision.summary).strip()
            await self.self_memory.upsert("user_corrections", merged)
            return 0
        if target == "experience":
            actions = tool_results or []
            outcome = "success"
            if any(not item.get("ok", True) for item in actions):
                outcome = "failure"
            elif actions:
                outcome = "success"
            else:
                outcome = "partial"
            return await self.experience.add(
                task_summary=user_text,
                actions=actions,
                outcome=outcome,
                lesson=decision.summary or assistant_text[:200],
            )
        kind = decision.kind or "fact"
        return await self.long_term.add(
            content=decision.summary or user_text,
            kind=kind,
            importance=max(0.0, min(1.0, decision.importance)),
            metadata={"user": user_text, "assistant": assistant_text[:400]},
        )

    async def add_identity_snapshot(self, yaml_text: str, note: str) -> None:
        """Store a raw identity YAML snapshot when evolution events occur."""
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO identity_snapshots (yaml_text, note, created_at) VALUES (?, ?, ?)",
            (yaml_text, note, _utc_now()),
        )
        await self._db.commit()

    async def upsert_task(self, session_id: str, task: dict[str, Any]) -> int:
        """Create or update working-memory task state."""
        assert self.working is not None
        return await self.working.save(session_id, task)

    async def self_check(self) -> dict[str, bool]:
        """Run a tiny write/read cycle against SQLite."""
        assert self._db is not None
        cursor = await self._db.execute("SELECT COUNT(*) AS n FROM memories")
        row = await cursor.fetchone()
        return {"db": True, "memories_table": row is not None}

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None


def _as_hit(item: dict[str, Any], layer: str) -> MemoryHit:
    """Convert a storage row into a MemoryHit."""
    content = str(item.get("content") or item.get("summary") or item.get("lesson") or "")
    metadata = {}
    raw = item.get("metadata_json")
    if isinstance(raw, str) and raw:
        try:
            metadata = json.loads(raw)
        except json.JSONDecodeError:
            metadata = {"raw": raw}
    return MemoryHit(
        id=int(item.get("id") or 0),
        layer=layer,
        content=content,
        score=float(item.get("score") or 0.0),
        metadata=metadata,
    )
