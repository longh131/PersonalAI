"""LLM gateway: adapters, prompt templates, and output parsers."""

from llm.adapters import ClaudeAdapter, DeepSeekAdapter, OpenAIAdapter
from llm.gateway import LLMGateway
from llm.parsers import OutputParser
from llm.prompts import PromptEngine

__all__ = [
    "ClaudeAdapter",
    "DeepSeekAdapter",
    "LLMGateway",
    "OpenAIAdapter",
    "OutputParser",
    "PromptEngine",
]


def verify_module() -> None:
    """Render a template and parse a persist JSON payload without a network call."""
    engine = PromptEngine()
    text = engine.render("persist", user_text="记住我叫李雷", assistant_text="好")
    assert "李雷" in text
    parser = OutputParser()
    decision = parser.parse_persist_decision(
        '{"should_save": true, "kind": "fact", "summary": "用户叫李雷", "importance": 0.9, "reason": "explicit", "target": "long_term"}'
    )
    assert decision.should_save
