"""Sensory layer: voice, vision, and the standby loop."""

from senses.standby import run_standby
from senses.vision import VisionIO
from senses.voice import VoiceIO

__all__ = ["VisionIO", "VoiceIO", "run_standby"]


def verify_module() -> None:
    """Ensure sensory classes import."""
    assert VoiceIO
    assert VisionIO
