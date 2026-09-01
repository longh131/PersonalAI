"""Screenshot, image load, and OCR ('scan') for the vision path."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from config.settings import PROJECT_ROOT


class VisionIO:
    """Local vision: mss screenshots + RapidOCR text extraction."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.available = False
        self._ocr: Any = None
        self.screenshot_dir = PROJECT_ROOT / "data" / "screenshots"

    async def initialize(self) -> None:
        """Create output dirs and load the OCR backend."""
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        if not self.enabled:
            logger.info("Vision disabled by settings")
            return
        await asyncio.to_thread(self._load_ocr)

    def _load_ocr(self) -> None:
        """Load RapidOCR; remain available for screenshots even if OCR fails."""
        try:
            from rapidocr_onnxruntime import RapidOCR

            self._ocr = RapidOCR()
            self.available = True
            logger.info("RapidOCR ready")
        except Exception as exc:  # noqa: BLE001
            self._ocr = None
            self.available = True
            logger.warning("OCR backend unavailable ({}). Screenshots still work.", exc)

    async def capture_screen(self, monitor: int = 1) -> Path:
        """Grab a monitor screenshot as PNG under `data/screenshots/`.

        Args:
            monitor: mss monitor index. 1 is the first display.

        Returns:
            Path to the written PNG.

        Raises:
            RuntimeError: If mss cannot capture.
        """
        if not self.enabled:
            raise RuntimeError("视觉功能已在设置中关闭")
        monitor = int(monitor or 1)

        def _grab() -> Path:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                monitors = sct.monitors
                index = monitor if 0 <= monitor < len(monitors) else 1
                raw = sct.grab(monitors[index])
                image = Image.frombytes("RGB", raw.size, raw.rgb)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.screenshot_dir / f"screen_{stamp}.png"
            image.save(path)
            return path

        path = await asyncio.to_thread(_grab)
        logger.info("Screenshot saved {}", path)
        return path

    async def analyze(self, path: str | Path) -> str:
        """OCR a local image and return a readable report for the LLM.

        Args:
            path: Image file path.

        Returns:
            Multi-line text including OCR lines or a fallback description.
        """
        target = Path(path)
        if not target.exists():
            return f"图片不存在：{target}"
        lines = await asyncio.to_thread(self._ocr_file, target)
        header = f"图片：{target.name}（{target}）"
        if not lines:
            return header + "\n未识别到文字。这可能是一张无文字照片；请用自然语言描述你想知道的部分。"
        body = "\n".join(f"- {line}" for line in lines)
        return f"{header}\nOCR 文字：\n{body}"

    def _ocr_file(self, path: Path) -> list[str]:
        """Run OCR synchronously. Returns an empty list when the backend is missing."""
        if self._ocr is None:
            return []
        try:
            result, _elapsed = self._ocr(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR failed: {}", exc)
            return []
        texts: list[str] = []
        for item in result or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                piece = str(item[1]).strip()
                if piece:
                    texts.append(piece)
        return texts

    async def self_check(self) -> dict[str, bool]:
        """Report whether screenshot dir and OCR backend are ready."""
        return {
            "enabled": self.enabled,
            "dir": self.screenshot_dir.exists(),
            "ocr": self._ocr is not None,
        }
