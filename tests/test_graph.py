"""LangGraph compilation and routing."""

from __future__ import annotations

import pytest

from core.graph import build_graph
from core.nodes import GraphNodes
from identity.loader import IdentityLoader
from llm.adapters import FakeLLMAdapter
from llm.gateway import LLMGateway
from memory.manager import MemoryManager
from tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_graph_compiles(memory: MemoryManager) -> None:
    """The six-node graph compiles without a live LLM call."""
    settings = memory.settings
    nodes = GraphNodes(
        memory=memory,
        llm=LLMGateway(settings, adapter=FakeLLMAdapter()),
        tools=ToolRegistry(),
        identity=IdentityLoader(),
        max_tool_iterations=3,
    )
    compiled = build_graph(nodes)
    assert compiled is not None


@pytest.mark.asyncio
async def test_route_after_reason_respects_iteration_cap(memory: MemoryManager) -> None:
    """When the tool-iteration cap is exceeded, the router goes to generate_reply."""
    settings = memory.settings
    nodes = GraphNodes(
        memory=memory,
        llm=LLMGateway(settings, adapter=FakeLLMAdapter()),
        tools=ToolRegistry(),
        identity=IdentityLoader(),
        max_tool_iterations=2,
    )
    state = {
        "decision": "use_tool",
        "tool_calls": [{"id": "1", "name": "web_search", "arguments": {}}],
        "iteration": 3,
    }
    assert nodes.route_after_reason(state) == "generate_reply"
    state["iteration"] = 1
    assert nodes.route_after_reason(state) == "execute_tools"
