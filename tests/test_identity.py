"""Identity YAML loading and prompt injection."""

from __future__ import annotations

from identity.loader import IdentityLoader
from identity.models import IdentityBundle


def test_identity_yaml_loads() -> None:
    """Default identity.yaml must contain all six sections."""
    bundle = IdentityLoader().load(force=True)
    assert isinstance(bundle, IdentityBundle)
    assert bundle.identity.name
    assert bundle.mission.primary
    assert bundle.personality.traits
    assert bundle.rules
    assert bundle.evolution.enabled is True


def test_identity_render_includes_memory_overlay() -> None:
    """Live user-model overlay from memory is injected into the system block."""
    text = IdentityLoader().render(memory_user_model="- 用户叫李雷", self_knowledge="- limits: 测试")
    assert "李雷" in text
    assert "limits" in text
    assert "Mission" in text or "使命" in text or "Mission" in text
