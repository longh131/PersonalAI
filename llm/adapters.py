"""LLM provider adapters. DeepSeek is first-class; GPT and Claude are ready to switch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger
from openai import AsyncOpenAI


@dataclass(slots=True)
class LLMResponse:
    """Normalized chat completion used by the gateway and graph nodes."""

    content: str
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class BaseLLMAdapter(ABC):
    """Common async chat interface for every provider."""

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> LLMResponse:
        """Send a chat completion request and return a normalized response."""


class DeepSeekAdapter(BaseLLMAdapter):
    """DeepSeek OpenAI-compatible Chat Completions API."""

    name = "deepseek"

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is missing")
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> LLMResponse:
        """Call DeepSeek chat completions."""
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        completion = await self._client.chat.completions.create(**kwargs)
        return _from_openai(completion, fallback_model=self.model)


class OpenAIAdapter(BaseLLMAdapter):
    """Official OpenAI Chat Completions API (reserved, fully wired)."""

    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing")
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> LLMResponse:
        """Call OpenAI chat completions."""
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        completion = await self._client.chat.completions.create(**kwargs)
        return _from_openai(completion, fallback_model=self.model)


class ClaudeAdapter(BaseLLMAdapter):
    """Anthropic Messages API via httpx (reserved, fully wired)."""

    name = "claude"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is missing")
        self.api_key = api_key
        self.model = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> LLMResponse:
        """Call Anthropic `/v1/messages` and map the result to `LLMResponse`."""
        system = ""
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                system = str(message.get("content") or "")
                continue
            if role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.get("tool_call_id"),
                                "content": str(message.get("content") or ""),
                            }
                        ],
                    }
                )
                continue
            converted.append({"role": role, "content": message.get("content") or ""})
        payload: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": 2048,
            "temperature": temperature,
            "messages": converted or [{"role": "user", "content": "你好"}],
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_to_anthropic_tool(tool) for tool in tools]
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content_bits: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                content_bits.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "function": {
                            "name": block.get("name"),
                            "arguments": json_dumps(block.get("input") or {}),
                        },
                    }
                )
        usage = data.get("usage") or {}
        return LLMResponse(
            content="".join(content_bits).strip(),
            tool_calls=tool_calls,
            raw={"tool_calls": tool_calls, "anthropic": data},
            model=str(data.get("model") or self.model),
            usage={
                "prompt_tokens": int(usage.get("input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
            },
        )


class FakeLLMAdapter(BaseLLMAdapter):
    """Deterministic adapter for tests and offline self-check."""

    name = "fake"

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = list(replies or ["这是测试回复。"])
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> LLMResponse:
        """Return the next canned reply without touching the network."""
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        content = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        user_text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                user_text = str(message.get("content") or "")
                break
        tool_calls: list[dict[str, Any]] = []
        if tools and any(marker in user_text for marker in ("搜索", "search", "搜一下")):
            tool_calls = [
                {
                    "id": "call_search",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "Personal AI OS"}',
                    },
                }
            ]
            content = ""
        if "记住" in user_text and "should_save" not in content:
            if "persist" in user_text or "判断" in "".join(str(m.get("content")) for m in messages[:1]):
                content = (
                    '{"should_save": true, "kind": "fact", "summary": "'
                    + user_text
                    + '", "importance": 0.9, "reason": "explicit", "target": "long_term"}'
                )
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            raw={"tool_calls": tool_calls},
            model=model or "fake",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def _from_openai(completion: Any, fallback_model: str) -> LLMResponse:
    """Convert an OpenAI SDK completion object into `LLMResponse`."""
    choice = completion.choices[0]
    message = choice.message
    content = message.content or ""
    tool_calls_raw: list[dict[str, Any]] = []
    for call in message.tool_calls or []:
        tool_calls_raw.append(
            {
                "id": call.id,
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
        )
    usage = completion.usage
    usage_dict = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }
    logger.debug("LLM {} tokens={}", completion.model or fallback_model, usage_dict)
    return LLMResponse(
        content=content,
        tool_calls=tool_calls_raw,
        raw={"tool_calls": tool_calls_raw},
        model=str(completion.model or fallback_model),
        usage=usage_dict,
    )


def _to_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Map an OpenAI tool schema onto Anthropic's tool format."""
    function = tool.get("function") or tool
    return {
        "name": function.get("name"),
        "description": function.get("description") or "",
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }


def json_dumps(data: Any) -> str:
    """Stable JSON serialization for tool arguments."""
    import json

    return json.dumps(data, ensure_ascii=False)
