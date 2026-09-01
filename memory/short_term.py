"""Short-term sliding-window memory for the current session."""

from __future__ import annotations

from collections import deque
from typing import Any


class ShortTermMemory:
    """In-process conversation window used as the recency layer.

    The window is also mirrored into SQLite `conversation_turns` by MemoryManager
    so a session can be reconstructed after restart.
    """

    def __init__(self, max_turns: int = 16) -> None:
        if max_turns < 2:
            raise ValueError("max_turns must be >= 2")
        self.max_turns = max_turns
        self._messages: deque[dict[str, Any]] = deque(maxlen=max_turns * 2)

    def append(self, message: dict[str, Any]) -> None:
        """Append one chat message to the sliding window.

        Args:
            message: OpenAI-style chat message mapping.
        """
        self._messages.append(dict(message))

    def window(self, n: int | None = None) -> list[dict[str, Any]]:
        """Return the newest messages, oldest first.

        Args:
            n: Optional max number of messages. Defaults to the full window.
        """
        items = list(self._messages)
        if n is None:
            return items
        return items[-n:]

    def clear(self) -> None:
        """Drop the in-memory window (does not delete SQLite history)."""
        self._messages.clear()

    def __len__(self) -> int:
        """Return the number of messages currently in the window."""
        return len(self._messages)
