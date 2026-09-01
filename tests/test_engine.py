"""End-to-end engine.process with a fake LLM."""

from __future__ import annotations

import pytest

from core.engine import PersonalAIEngine
from memory.manager import MemoryManager


@pytest.mark.asyncio
async def test_process_greeting_returns_text(engine: PersonalAIEngine) -> None:
    """A greeting runs the full graph and returns the fake adapter reply."""
    reply = await engine.process("你好")
    assert isinstance(reply, str)
    assert reply
    health = await engine.self_check()
    assert health["graph"] is True
    assert health["tools"]["read_file"] is True


@pytest.mark.asyncio
async def test_process_remember_writes_memory(
    engine: PersonalAIEngine, memory: MemoryManager
) -> None:
    """Explicit remember flows through the graph into long-term memory."""
    reply = await engine.process("记住我叫韩梅梅")
    assert reply
    hits = await memory.search("韩梅梅", k=5)
    assert any("韩梅梅" in hit.content for hit in hits)
