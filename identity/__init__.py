"""Identity module: YAML-backed persona injected into every LLM call."""

from identity.loader import IdentityLoader
from identity.models import IdentityBundle

__all__ = ["IdentityBundle", "IdentityLoader"]


def verify_module() -> None:
    """Load the default identity YAML and ensure required sections exist."""
    loader = IdentityLoader()
    bundle = loader.load()
    assert bundle.identity.name
    assert bundle.mission.primary
    assert loader.render(bundle)
