"""Shared fixtures: isolated SQLite, hash embeddings, fake LLM, dummy vision."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from core.engine import PersonalAIEngine
from llm.adapters import FakeLLMAdapter
from memory.manager import MemoryManager, hash_embed


class DummyVision:
    """Vision stub that never touches mss/OCR."""

    enabled = True

    async def initialize(self) -> None:
        """No-op initialize for tests."""
        return None

    async def analyze(self, path: str | Path) -> str:
        """Return a deterministic OCR-like blob."""
        return f"图片：{path}\nOCR 文字：\n- dummy-ocr"

    async def capture_screen(self, monitor: int = 1) -> Path:
        """Pretend a screenshot was written."""
        return Path("data/screenshots/dummy.png")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at a temp database and the fake LLM provider."""
    return Settings(
        sqlite_path=str(tmp_path / "test.db"),
        llm_provider="fake",
        deepseek_api_key="test-key",
        voice_enabled=False,
        tts_enabled=False,
        vision_enabled=False,
        workspace_root=str(Path(__file__).resolve().parents[1]),
    )


@pytest.fixture
async def memory(settings: Settings) -> MemoryManager:
    """Initialized memory manager using hash embeddings (no model download)."""
    manager = MemoryManager(settings, encode=hash_embed)
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
async def engine(settings: Settings, memory: MemoryManager) -> PersonalAIEngine:
    """Engine wired to FakeLLMAdapter and DummyVision."""
    instance = PersonalAIEngine(
        settings,
        memory=memory,
        adapter=FakeLLMAdapter(["收到，我是织。测试通道正常。"]),
        vision=DummyVision(),
        embedder=memory.embedder,
    )
    await instance.initialize()
    yield instance
    await instance.shutdown()
