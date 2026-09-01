"""Prompt templates and persist JSON parsing."""

from __future__ import annotations

from llm.parsers import OutputParser
from llm.prompts import PromptEngine


def test_prompt_render_reason() -> None:
    """Reason template substitutes identity and memory blocks."""
    text = PromptEngine().render(
        "reason",
        identity_block="Name: 织",
        memory_block="- 用户叫李雷",
        working_block="无",
        vision_block="无",
    )
    assert "织" in text
    assert "李雷" in text


def test_parse_persist_decision_from_fence() -> None:
    """Parser accepts fenced JSON and clamps importance."""
    raw = """```json
    {"should_save": true, "kind": "preference", "summary": "喜欢浓缩咖啡",
     "importance": 1.7, "reason": "stated", "target": "long_term"}
    ```"""
    decision = OutputParser().parse_persist_decision(raw)
    assert decision.should_save is True
    assert decision.kind == "preference"
    assert decision.importance == 1.0


def test_parse_tool_calls() -> None:
    """OpenAI-style tool_calls are normalized to id/name/arguments."""
    parsed = OutputParser().parse_tool_calls(
        {
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {"name": "web_search", "arguments": '{"query": "aion"}'},
                }
            ]
        }
    )
    assert parsed[0]["name"] == "web_search"
    assert parsed[0]["arguments"]["query"] == "aion"
