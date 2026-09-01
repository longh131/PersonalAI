"""Parsers for tool calls, JSON blobs, and persist decisions."""

from __future__ import annotations

import json
import re
from typing import Any

from memory.manager import PersistDecision


class OutputParser:
    """Turns messy model text into structured objects."""

    def parse_tool_calls(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize OpenAI-style tool_calls from an adapter response.

        Args:
            payload: Raw `LLMResponse.raw` or a mapping with `tool_calls`.

        Returns:
            A list of `{id, name, arguments}` dicts.
        """
        raw_calls = payload.get("tool_calls") or []
        parsed: list[dict[str, Any]] = []
        for call in raw_calls:
            function = call.get("function") or {}
            arguments = function.get("arguments") or call.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments}
            parsed.append(
                {
                    "id": str(call.get("id") or f"call_{len(parsed)}"),
                    "name": str(function.get("name") or call.get("name") or ""),
                    "arguments": arguments if isinstance(arguments, dict) else {"value": arguments},
                }
            )
        return [item for item in parsed if item["name"]]

    def parse_json(self, text: str) -> dict[str, Any]:
        """Extract the first JSON object from a model response."""
        blob = _extract_json_object(text)
        data = json.loads(blob)
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")
        return data

    def parse_persist_decision(self, text: str) -> PersistDecision:
        """Parse a persist-decision JSON payload into `PersistDecision`.

        Args:
            text: Model output that should contain a JSON object.

        Returns:
            PersistDecision with safe defaults when parsing fails.
        """
        try:
            data = self.parse_json(text)
        except (ValueError, json.JSONDecodeError):
            return PersistDecision(False, None, "", 0.0, "unparseable model output")
        kind = data.get("kind")
        if kind not in {None, "fact", "preference", "event", "relationship", "knowledge"}:
            kind = "fact"
        target = data.get("target") or "long_term"
        if target not in {"long_term", "self", "experience"}:
            target = "long_term"
        try:
            importance = float(data.get("importance") or 0.0)
        except (TypeError, ValueError):
            importance = 0.0
        return PersistDecision(
            should_save=bool(data.get("should_save")),
            kind=kind,
            summary=str(data.get("summary") or ""),
            importance=max(0.0, min(1.0, importance)),
            reason=str(data.get("reason") or ""),
            target=str(target),
        )


def _extract_json_object(text: str) -> str:
    """Pull a JSON object out of fenced markdown or mixed prose."""
    stripped = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("No JSON object found")
