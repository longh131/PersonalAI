"""Memory layers: persist gating, search, and conversation log."""

from __future__ import annotations

import pytest

from memory.manager import MemoryManager, hash_embed


@pytest.mark.asyncio
async def test_hash_embed_is_stable() -> None:
    """Fallback embeddings are deterministic for the same string."""
    a = hash_embed("personal-ai")
    b = hash_embed("personal-ai")
    assert a.shape == b.shape
    assert float(abs(a - b).max()) == 0.0


@pytest.mark.asyncio
async def test_greeting_is_not_persisted(memory: MemoryManager) -> None:
    """Short greetings must not create long-term rows."""
    decision = memory.heuristic_decision("你好", "我在。")
    assert decision.should_save is False
    inserted = await memory.maybe_persist("你好", "我在。", decision)
    assert inserted is None
    background = memory.heuristic_decision("[后台]搜索公开资料", "好。")
    assert background.should_save is False


@pytest.mark.asyncio
async def test_explicit_remember_and_search(memory: MemoryManager) -> None:
    """「记住」writes a long-term fact that semantic search can retrieve."""
    await memory.ensure_session("s1")
    decision = memory.heuristic_decision("记住我叫李雷", "好，我记住了。")
    assert decision.should_save is True
    row_id = await memory.maybe_persist("记住我叫李雷", "好，我记住了。", decision)
    assert row_id
    hits = await memory.search("我叫什么名字", k=5)
    assert hits
    assert any("李雷" in hit.content for hit in hits)


@pytest.mark.asyncio
async def test_conversation_turns_are_always_logged(memory: MemoryManager) -> None:
    """Every turn is stored in conversation_turns even when long-term is skipped."""
    await memory.ensure_session("s2")
    await memory.remember_turn("s2", "user", "你好")
    await memory.remember_turn("s2", "assistant", "我在。")
    assert len(memory.short_term.window()) == 2
    assert memory._db is not None
    cursor = await memory._db.execute("SELECT COUNT(*) AS n FROM conversation_turns")
    row = await cursor.fetchone()
    assert int(row["n"]) == 2
