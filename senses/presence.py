"""Visible assistant status: console tag + tray icon color."""

from __future__ import annotations

from typing import Any

from loguru import logger

# status → (label, RGBA fill)
STATUS_META: dict[str, tuple[str, tuple[int, int, int, int]]] = {
    "idle": ("待机", (46, 140, 220, 255)),
    "listening": ("聆听中", (46, 180, 90, 255)),
    "thinking": ("思考中", (210, 150, 36, 255)),
    "speaking": ("播报中", (220, 228, 240, 255)),
}


def status_label(status: str) -> str:
    """Return the Chinese label for a presence status."""
    return STATUS_META.get(status, STATUS_META["idle"])[0]


def make_status_icon(status: str) -> Any:
    """Draw a 64px tray disc whose fill color matches the current status."""
    from PIL import Image, ImageDraw

    color = STATUS_META.get(status, STATUS_META["idle"])[1]
    image = Image.new("RGBA", (64, 64), (16, 18, 24, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 62, 62), fill=color)
    draw.ellipse((18, 18, 46, 46), outline=(240, 248, 255, 255), width=4)
    return image


class Presence:
    """Fan-out helper: print 【状态】 and update the tray icon."""

    def __init__(self, name: str, *, tray: Any | None = None) -> None:
        self.name = name
        self.tray = tray
        self.status = ""

    def set(self, status: str) -> None:
        """Switch status if it changed. Safe to call from the standby loop."""
        if status == self.status:
            return
        self.status = status
        print(f"【{status_label(status)}】", flush=True)
        if self.tray is not None:
            try:
                self.tray.set_status(status)
            except Exception as exc:  # noqa: BLE001
                logger.debug("tray set_status: {}", exc)
