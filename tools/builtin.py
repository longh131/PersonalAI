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
from memory.manager import MemoryManager
from memory.reminders import format_reminder_list, parse_due
from tools.calendar import fetch_outlook_agenda
from tools.desktop import DesktopTools
from tools.health import format_status, read_system_snapshot
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


CONFIRM_TOKENS = ("确认", "确定", "confirm")


def _flag(value: Any, default: bool = False) -> bool:
    """Coerce LLM-supplied booleans that often arrive as strings."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


class BuiltinTools:
    """Factory that binds workspace-aware tool implementations."""

    def __init__(
        self,
        settings: Settings,
        vision: Any | None = None,
        reminders: Any | None = None,
        memory: MemoryManager | None = None,
        jobs: Any | None = None,
    ) -> None:
        self.settings = settings
        self.workspace = settings.resolve_workspace()
        self.vision = vision
        self.desktop = DesktopTools()
        self.memory = memory
        self.reminders = reminders
        self.jobs = jobs
        self.last_user_text = ""
        if self.reminders is None and memory is not None:
            self.reminders = memory.reminders

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
            "foreground_window": self.desktop.foreground_window,
            "clipboard_text": self.desktop.clipboard_text,
            "list_windows": self.desktop.list_windows,
            "open_app": self.desktop.open_app,
            "focus_window": self.desktop.focus_window,
            "calendar_agenda": self.calendar_agenda,
            "set_reminder": self.set_reminder,
            "list_reminders": self.list_reminders,
            "system_status": self.system_status,
            "forget_memory": self.forget_memory,
            "correct_memory": self.correct_memory,
            "upsert_entity": self.upsert_entity,
            "list_entities": self.list_entities,
            "forget_entity": self.forget_entity,
            "set_protocol": self.set_protocol,
            "list_protocols": self.list_protocols,
            "daily_briefing": self.daily_briefing,
            "background_task": self.background_task,
            "set_volume": self.set_volume,
            "set_dnd": self.set_dnd,
            "lock_pc": self.lock_pc,
            "delete_file": self.delete_file,
            "power_action": self.power_action,
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
        """Search the public web. Bing first — ddgs auto often times out in CN."""
        from tools.websearch import format_search_rows, search_web

        limit = max(1, min(int(max_results or 5), 8))
        timeout = float(getattr(self.settings, "web_search_timeout", 8.0) or 8.0)
        proxy = (getattr(self.settings, "web_search_proxy", "") or "").strip() or None
        backend = (getattr(self.settings, "web_search_backend", "bing") or "bing").strip()

        def _search() -> list[dict[str, Any]]:
            return search_web(
                query,
                max_results=limit,
                timeout=timeout,
                proxy=proxy,
                backend=backend,
            )

        try:
            rows = await asyncio.wait_for(asyncio.to_thread(_search), timeout=timeout + 2)
        except TimeoutError:
            logger.warning("web_search timed out after {:.0f}s", timeout + 2)
            return "网页搜索超时。本机连不上海外搜索源时，请确认必应能打开，或在 .env 设置 WEB_SEARCH_PROXY。"
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_search failed: {}", exc)
            return f"网页搜索失败：{exc}"
        return format_search_rows(rows)

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

    async def calendar_agenda(self, days: int = 1, include_tasks: bool = True) -> str:
        """Read-only Outlook agenda for the next few days."""
        want_tasks = include_tasks
        if isinstance(include_tasks, str):
            want_tasks = include_tasks.strip().lower() not in {"0", "false", "no", "off"}
        return await fetch_outlook_agenda(days=int(days or 1), include_tasks=bool(want_tasks))

    async def set_reminder(
        self,
        message: str,
        delay_minutes: float | None = None,
        at_local: str | None = None,
        delay_seconds: float | None = None,
    ) -> str:
        """Schedule a local spoken reminder."""
        if self.reminders is None:
            return "提醒存储未就绪。"
        try:
            due = parse_due(delay_minutes=delay_minutes, delay_seconds=delay_seconds, at_local=at_local)
        except ValueError as exc:
            return f"无法设定提醒：{exc}"
        item = await self.reminders.add(message, due)
        return f"已设定提醒 #{item.id}，{item.due_local_text()}：{item.message}"

    async def list_reminders(self, cancel_id: int | None = None) -> str:
        """List pending reminders, or cancel one by id."""
        if self.reminders is None:
            return "提醒存储未就绪。"
        if cancel_id is not None and str(cancel_id) != "":
            ok = await self.reminders.cancel(int(cancel_id))
            leftover = format_reminder_list(await self.reminders.list_pending())
            if ok:
                return f"已取消提醒 #{int(cancel_id)}。\n{leftover}"
            return f"找不到待取消的提醒 #{int(cancel_id)}。\n{leftover}"
        return format_reminder_list(await self.reminders.list_pending())

    async def system_status(self) -> str:
        """Report battery, disk, and network."""
        return format_status(read_system_snapshot())

    async def forget_memory(self, query: str = "", memory_id: int | None = None) -> str:
        if self.memory is None:
            return "记忆存储未就绪。"
        return await self.memory.forget_memories(query=query, memory_id=memory_id)

    async def correct_memory(self, query: str, replacement: str) -> str:
        if self.memory is None:
            return "记忆存储未就绪。"
        return await self.memory.correct_memory(query, replacement)

    async def upsert_entity(self, kind: str, name: str, relation: str = "", notes: str = "") -> str:
        if self.memory is None or self.memory.entities is None:
            return "记忆存储未就绪。"
        return await self.memory.entities.upsert(kind, name, relation=relation, notes=notes)

    async def list_entities(self, kind: str = "") -> str:
        if self.memory is None or self.memory.entities is None:
            return "记忆存储未就绪。"
        rows = await self.memory.entities.list_active(kind)
        return self.memory.entities.format_list(rows)

    async def forget_entity(self, name: str, kind: str = "") -> str:
        if self.memory is None or self.memory.entities is None:
            return "记忆存储未就绪。"
        return await self.memory.entities.forget(name, kind=kind)

    async def set_protocol(
        self,
        name: str,
        enabled: bool = True,
        start: str = "",
        end: str = "",
    ) -> str:
        if self.memory is None or self.memory.protocols is None:
            return "协议存储未就绪。"
        flag = enabled
        if isinstance(enabled, str):
            flag = enabled.strip().lower() not in {"0", "false", "no", "off"}
        config: dict[str, Any] = {}
        if start:
            config["start"] = start
        if end:
            config["end"] = end
        return await self.memory.protocols.set(name, bool(flag), config or None)

    async def list_protocols(self) -> str:
        if self.memory is None or self.memory.protocols is None:
            return "协议存储未就绪。"
        rows = await self.memory.protocols.list_all()
        return self.memory.protocols.format_list(rows)

    def _inside_workspace(self, target: Path) -> bool:
        try:
            target.resolve().relative_to(self.workspace.resolve())
            return True
        except ValueError:
            return False

    def _user_said_confirm(self, confirm: Any) -> bool:
        if not _flag(confirm, False):
            return False
        text = self.last_user_text or ""
        lowered = text.lower()
        return any(token in text or token in lowered for token in CONFIRM_TOKENS)

    async def daily_briefing(self, kind: str = "auto") -> str:
        from tools.briefing import auto_briefing_kind, compose_briefing

        wanted = (kind or "auto").strip().lower()
        if wanted in {"auto", "自动", ""}:
            wanted = auto_briefing_kind() or "morning"
        if wanted in {"早报", "晨间", "今天", "morning"}:
            wanted = "morning"
        if wanted in {"离开", "下班", "走了", "leaving"}:
            wanted = "leaving"
        reminders = []
        if self.reminders is not None:
            reminders = await self.reminders.list_pending()
        return await compose_briefing(wanted, reminders=reminders)

    async def background_task(self, instruction: str) -> str:
        if self.jobs is None:
            return "后台队列未就绪。"
        return self.jobs.submit(instruction)

    async def set_volume(self, action: str = "", percent: int | None = None) -> str:
        from tools.volume import set_volume_percent, volume_nudge

        if percent is not None and str(percent) != "":
            return await asyncio.to_thread(set_volume_percent, int(percent))
        return await asyncio.to_thread(volume_nudge, action or "up")

    async def set_dnd(self, enabled: bool = True) -> str:
        return await self.set_protocol("mute", enabled=_flag(enabled, True))

    async def lock_pc(self) -> str:
        from tools.volume import lock_workstation

        return await asyncio.to_thread(lock_workstation)

    async def delete_file(self, path: str, confirm: bool = False) -> str:
        target = self._resolve_path(path)
        if not self._inside_workspace(target):
            return f"只能删除工作区内的文件：{target}"
        if not self._user_said_confirm(confirm):
            return f"删除文件需要你本轮明确说「确认」。将删除：{target}"
        if not target.exists():
            return f"文件不存在：{target}"
        if not target.is_file():
            return "只能删除文件，不能删目录。"
        await asyncio.to_thread(target.unlink)
        return f"已删除：{target}"

    async def power_action(self, action: str, confirm: bool = False) -> str:
        if not self._user_said_confirm(confirm):
            return "关机、重启、睡眠需要你本轮明确说「确认」。例如：确认关机。"
        from tools.volume import sleep_or_power

        return await asyncio.to_thread(sleep_or_power, action)


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
