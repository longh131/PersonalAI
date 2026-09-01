"""Five-layer memory system with a single MemoryManager facade."""

from memory.experience import ExperienceMemory
from memory.long_term import LongTermMemory
from memory.manager import MemoryManager, TextEmbedder, hash_embed
from memory.self_memory import SelfMemory
from memory.short_term import ShortTermMemory
from memory.working import WorkingMemory

__all__ = [
    "ExperienceMemory",
    "LongTermMemory",
    "MemoryManager",
    "SelfMemory",
    "ShortTermMemory",
    "TextEmbedder",
    "WorkingMemory",
    "hash_embed",
]


def verify_module() -> None:
    """Ensure memory layer classes are importable and embedder fallback works."""
    vector = hash_embed("personal-ai-os")
    assert vector.shape[0] == 384
    assert ShortTermMemory(max_turns=4)
    assert MemoryManager
