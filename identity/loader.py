"""Load identity YAML and render it into an injectable system block."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from config.settings import PROJECT_ROOT
from identity.models import IdentityBundle


class IdentityLoader:
    """Reads `config/identity.yaml` and turns it into prompt context."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (PROJECT_ROOT / "config" / "identity.yaml")
        self._cached: IdentityBundle | None = None

    def load(self, *, force: bool = False) -> IdentityBundle:
        """Parse and validate the identity YAML.

        Args:
            force: Ignore the in-process cache and reload from disk.

        Returns:
            Validated identity bundle.

        Raises:
            FileNotFoundError: If the YAML file is missing.
            ValueError: If the YAML cannot be parsed or validated.
        """
        if self._cached is not None and not force:
            return self._cached
        if not self.path.exists():
            raise FileNotFoundError(f"Identity file not found: {self.path}")
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("identity.yaml must contain a mapping")
        bundle = IdentityBundle.model_validate(raw)
        self._cached = bundle
        logger.debug("Loaded identity for {}", bundle.identity.name)
        return bundle

    def render(
        self,
        bundle: IdentityBundle | None = None,
        *,
        memory_user_model: str = "",
        self_knowledge: str = "",
    ) -> str:
        """Render a system-prompt block from identity plus live memory overlays.

        Args:
            bundle: Optional preloaded bundle; loads from disk when omitted.
            memory_user_model: Facts/preferences retrieved from long-term memory.
            self_knowledge: Entries from self memory.

        Returns:
            Markdown-like text to inject as the system identity context.
        """
        bundle = bundle or self.load()
        ident = bundle.identity
        lines: list[str] = [
            "# Identity",
            f"Name: {ident.name}",
            f"Codename: {ident.codename}" if ident.codename else "",
            f"Role: {ident.role}",
            ident.self_description.strip(),
            "",
            "# Mission",
            bundle.mission.primary.strip(),
            "Principles:",
            *[f"- {item}" for item in bundle.mission.principles],
            "",
            "# Personality",
            "Traits: " + "、".join(bundle.personality.traits),
            bundle.personality.speaking_style.strip(),
            f"Address the user as: {bundle.personality.address_user_as}",
            "",
            "# User model",
            f"Display name: {bundle.user_model.display_name or '（由记忆补充）'}",
            f"Timezone: {bundle.user_model.timezone}",
            f"Language: {bundle.user_model.language}",
            bundle.user_model.notes.strip(),
        ]
        if memory_user_model.strip():
            lines.extend(["", "## Live user model from memory", memory_user_model.strip()])
        lines.extend(["", "# Rules", *[f"- {rule}" for rule in bundle.rules]])
        if self_knowledge.strip():
            lines.extend(["", "# Self knowledge", self_knowledge.strip()])
        lines.extend(
            [
                "",
                "# Evolution",
                f"Enabled: {bundle.evolution.enabled}",
                bundle.evolution.method.strip(),
            ]
        )
        return "\n".join(line for line in lines if line is not None)

    def snapshot_text(self) -> str:
        """Return the raw YAML text for identity snapshot persistence."""
        return self.path.read_text(encoding="utf-8")


def overlay_from_hits(hits: list[Any]) -> str:
    """Format memory hits into a compact user-model overlay.

    Args:
        hits: Objects with a `content` attribute or mapping with `content`.

    Returns:
        Bullet list text, or an empty string when there are no hits.
    """
    bullets: list[str] = []
    for hit in hits:
        content = getattr(hit, "content", None)
        if content is None and isinstance(hit, dict):
            content = hit.get("content")
        if content:
            bullets.append(f"- {content}")
    return "\n".join(bullets)
