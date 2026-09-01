"""Unified LLM gateway that hides provider differences."""

from __future__ import annotations

from typing import Any

from loguru import logger

from config.settings import Settings
from llm.adapters import (
    BaseLLMAdapter,
    ClaudeAdapter,
    DeepSeekAdapter,
    FakeLLMAdapter,
    LLMResponse,
    OpenAIAdapter,
)
from llm.parsers import OutputParser
from llm.prompts import PromptEngine


class LLMGateway:
    """Single entry for chat, reasoning, templates and parsing."""

    def __init__(self, settings: Settings, adapter: BaseLLMAdapter | None = None) -> None:
        self.settings = settings
        self.templates = PromptEngine()
        self.parser = OutputParser()
        self.adapter = adapter or self._build_adapter(settings)

    @staticmethod
    def _build_adapter(settings: Settings) -> BaseLLMAdapter:
        """Instantiate the adapter selected by `LLM_PROVIDER`."""
        provider = (settings.llm_provider or "deepseek").strip().lower()
        if provider == "openai":
            return OpenAIAdapter(settings.openai_api_key, settings.openai_base_url, settings.openai_model)
        if provider == "claude":
            return ClaudeAdapter(settings.anthropic_api_key, settings.anthropic_model)
        if provider == "fake":
            return FakeLLMAdapter()
        return DeepSeekAdapter(
            settings.deepseek_api_key,
            settings.deepseek_base_url,
            settings.deepseek_model,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send a chat request through the active adapter.

        Args:
            messages: OpenAI-style chat messages.
            tools: Optional function-calling schemas.
            temperature: Sampling temperature.
        """
        logger.debug("LLM chat via {} tools={}", self.adapter.name, bool(tools))
        return await self.adapter.chat(messages, tools=tools, temperature=temperature)

    async def reason(
        self,
        system: str,
        user: str,
        *,
        history: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        vision_messages: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Reasoning-node helper: system + history + current user (+ optional image)."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        user_message: dict[str, Any] = {"role": "user", "content": user}
        if vision_messages:
            messages.extend(vision_messages)
        else:
            messages.append(user_message)
        return await self.chat(messages, tools=tools, temperature=0.3)

    def render(self, template_name: str, **kwargs: Any) -> str:
        """Render a named prompt template."""
        return self.templates.render(template_name, **kwargs)

    async def self_check(self) -> dict[str, bool]:
        """Return adapter identity without making a paid API call."""
        return {"adapter": bool(self.adapter.name), "provider": True}
