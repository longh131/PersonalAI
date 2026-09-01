"""Built-in tools: files, web search, sandboxed code, screenshot and OCR."""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from config.settings import PROJECT_ROOT, Settings
from tools.registry import FunctionTool, ToolRegistry
from tools.schemas import schema_by_name

DENIED_CODE = (
    "os.system",
    "subprocess",
    "shutil.rmtree",
    "shutil.move",
    "ctypes",
    "winreg",
    "socket",
    "Path.unlink",
    "os.remove",
    "os.rmdir",
    "open(__",
)


class BuiltinTools:
    """Factory that binds workspace-aware tool implementations."""

    def __init__(self, settings: Settings, vision: Any | None = None) -> None:
        self.settings = settings
        self.workspace = settings.resolve_workspace()
        self.vision = vision

    def register_all(self, registry: ToolRegistry) -> None:
        """Register every built-in tool onto the given registry."""
        schemas = schema_by_name()
        mapping = {
            "read_file": self.read_file,
            "search_files": self.search_files,
            "web_search": self.web_search,
            "execute_code": self.execute_code,
            "capture_screen": self.capture_screen,
            "analyze_image": self.analyze_image,
        }
        for name, handler in mapping.items():
            schema = schemas[name]
            registry.register(
                FunctionTool(
                    name=name,
                    description=schema["function"]["description"],
                    schema=schema,
                    handler=handler,
                )
            )

    def _resolve_path(self, path: str) -> Path:
        """Resolve a user-supplied path against the workspace root."""
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        return candidate.resolve()

    async def read_file(self, path: str) -> str:
        """Read a local text file (truncated to keep prompts small)."""
        target = self._resolve_path(path)
        if not target.exists():
            return f"文件不存在：{target}"
        if not target.is_file():
            return f"不是文件：{target}"
        data = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        if len(data) > 12000:
            data = data[:12000] + "\n…(truncated)"
        return data

    async def search_files(self, pattern: str, root: str | None = None) -> str:
        """Glob-search files under the workspace or an optional root."""
        base = self._resolve_path(root) if root else self.workspace
        if not base.exists():
            return f"搜索根目录不存在：{base}"
        matches = await asyncio.to_thread(lambda: sorted(base.glob(pattern))[:50])
        if not matches:
            return "没有匹配的文件。"
        lines = []
        for item in matches:
            try:
                relative = item.relative_to(self.workspace)
            except ValueError:
                relative = item
            lines.append(str(relative))
        return "\n".join(lines)

    async def web_search(self, query: str, max_results: int = 5) -> str:
        """Search the public web via ddgs."""
        limit = max(1, min(int(max_results or 5), 8))

        def _search() -> list[dict[str, Any]]:
            from ddgs import DDGS

            with DDGS() as client:
                return list(client.text(query, max_results=limit))

        try:
            rows = await asyncio.to_thread(_search)
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_search failed: {}", exc)
            return f"网页搜索失败：{exc}"
        if not rows:
            return "没有搜索结果。"
        chunks: list[str] = []
        for index, row in enumerate(rows, start=1):
            title = row.get("title") or ""
            href = row.get("href") or row.get("url") or ""
            body = row.get("body") or row.get("snippet") or ""
            chunks.append(f"{index}. {title}\n{href}\n{body}")
        return "\n\n".join(chunks)

    async def execute_code(self, code: str) -> str:
        """Run short Python in a temp directory with a denylist and timeout."""
        source = code or ""
        for token in DENIED_CODE:
            if token in source:
                return f"拒绝执行：代码包含不允许的操作 `{token}`。"
        tmp_dir = Path(tempfile.mkdtemp(prefix="aion_code_"))
        script = tmp_dir / "snippet.py"
        script.write_text(source, encoding="utf-8")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script),
                cwd=str(tmp_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            except TimeoutError:
                process.kill()
                return "代码执行超时（10 秒）。"
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            if process.returncode != 0:
                return f"退出码 {process.returncode}\n{err or out}"
            return out or "(无输出)"
        finally:
            try:
                script.unlink(missing_ok=True)
                tmp_dir.rmdir()
            except OSError:
                pass

    async def capture_screen(self, monitor: int = 1) -> str:
        """Take a screenshot and run OCR / vision analysis."""
        if self.vision is None:
            return "视觉模块未初始化。"
        path = await self.vision.capture_screen(monitor=monitor)
        analysis = await self.vision.analyze(path)
        return f"截屏已保存：{path}\n{analysis}"

    async def analyze_image(self, path: str) -> str:
        """OCR and describe a local image."""
        if self.vision is None:
            return "视觉模块未初始化。"
        target = self._resolve_path(path)
        if not target.exists():
            return f"图片不存在：{target}"
        return await self.vision.analyze(target)


def looks_like_image_path(text: str) -> bool:
    """Return True when the utterance looks like a path to an image file."""
    stripped = text.strip().strip('"')
    return bool(re.search(r"\.(png|jpe?g|webp|bmp|gif|tiff?)$", stripped, re.IGNORECASE))


def verify_module() -> None:
    """Ensure schemas cover the four required tools."""
    names = set(schema_by_name())
    for required in ("read_file", "search_files", "web_search", "execute_code"):
        assert required in names
    assert PROJECT_ROOT.exists()
