"""FIFO queue for long work that should not block the voice loop."""

from __future__ import annotations

import queue


class JobQueue:
    """Thread-safe pending instructions for the standby worker."""

    def __init__(self) -> None:
        self._pending: queue.Queue[str] = queue.Queue()
        self.current = ""

    def submit(self, instruction: str) -> str:
        """Enqueue work. Returns a short ack for the LLM to tell the user."""
        text = (instruction or "").strip()
        if not text:
            return "没有后台任务内容。"
        self._pending.put(text)
        n = self._pending.qsize()
        shown = text if len(text) <= 40 else text[:40] + "…"
        return f"已在后台排队（第 {n} 件）：{shown}。你可以先聊别的。"

    def pop_nowait(self) -> str | None:
        """Take the next instruction, or None when idle."""
        try:
            return self._pending.get_nowait()
        except queue.Empty:
            return None

    def pending_count(self) -> int:
        return self._pending.qsize()
