"""Personal AI Core package."""

from core.engine import PersonalAIEngine
from core.state import AgentState, empty_state

__all__ = ["AgentState", "PersonalAIEngine", "empty_state"]


def verify_module() -> None:
    """Ensure the engine class and state factory import cleanly."""
    assert PersonalAIEngine
    state = empty_state("s", "hello")
    assert state["user_input"] == "hello"
