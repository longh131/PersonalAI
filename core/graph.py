"""Compile the Personal AI Core StateGraph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from core.nodes import GraphNodes
from core.state import AgentState


def build_graph(nodes: GraphNodes) -> Any:
    """Wire the six-node workflow with a bounded ReAct tool loop.

    Args:
        nodes: Bound node implementations.

    Returns:
        A compiled LangGraph runnable.
    """
    graph = StateGraph(AgentState)
    graph.add_node("parse_input", nodes.parse_input)
    graph.add_node("weave_context", nodes.weave_context)
    graph.add_node("reason_decide", nodes.reason_decide)
    graph.add_node("execute_tools", nodes.execute_tools)
    graph.add_node("generate_reply", nodes.generate_reply)
    graph.add_node("update_memory", nodes.update_memory)
    graph.add_edge(START, "parse_input")
    graph.add_edge("parse_input", "weave_context")
    graph.add_edge("weave_context", "reason_decide")
    graph.add_conditional_edges(
        "reason_decide",
        nodes.route_after_reason,
        {
            "execute_tools": "execute_tools",
            "generate_reply": "generate_reply",
        },
    )
    graph.add_edge("execute_tools", "reason_decide")
    graph.add_edge("generate_reply", "update_memory")
    graph.add_edge("update_memory", END)
    return graph.compile()
