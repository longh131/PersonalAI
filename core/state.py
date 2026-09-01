"""LangGraph shared state for one user turn."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

import operator

InputType = Literal["text", "voice", "video", "image"]
Decision = Literal["respond", "use_tool"]


class AgentState(TypedDict, total=False):
    """Mutable graph state. List fields use append reducers."""

    session_id: str
    user_input: str
    input_type: InputType
    image_path: str
    parsed_intent: dict[str, Any]
    identity_block: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    retrieved_memories: list[dict[str, Any]]
    working_snapshot: dict[str, Any]
    vision_block: str
    reasoning: str
    decision: Decision
    tool_calls: list[dict[str, Any]]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    iteration: int
    response: str
    persist_decision: dict[str, Any]
    error: str | None


def empty_state(
    session_id: str,
    user_input: str,
    *,
    input_type: InputType = "text",
    image_path: str = "",
) -> AgentState:
    """Build the initial state dict for `graph.ainvoke`."""
    return {
        "session_id": session_id,
        "user_input": user_input,
        "input_type": input_type,
        "image_path": image_path,
        "parsed_intent": {},
        "identity_block": "",
        "messages": [],
        "retrieved_memories": [],
        "working_snapshot": {},
        "vision_block": "",
        "reasoning": "",
        "decision": "respond",
        "tool_calls": [],
        "tool_results": [],
        "iteration": 0,
        "response": "",
        "persist_decision": {},
        "error": None,
    }
