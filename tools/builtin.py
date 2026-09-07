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
from tools.library import (
    default_browse_subdir,
    is_code_name,
    library_subdir_for,
    user_desktop,
)
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
        self.library = settings.resolve_library()
        self.desktop_dir = user_desktop()
        self.vision = vision
        self.desktop = DesktopTools()
        self.memory = memory
        self.reminders = reminders
        self.jobs = jobs
        self.last_user_text = ""
        self.env_path = PROJECT_ROOT / ".env"
        if self.reminders is None and memory is not None:
            self.reminders = memory.reminders

    def register_all(self, registry: ToolRegistry) -> None:
        """Register every built-in tool onto the given registry."""
        schemas = schema_by_name()
        mapping = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "search_files": self.search_files,
            "web_search": self.web_search,
            "execute_code": self.execute_code,
            "capture_screen": self.capture_screen,
            "analyze_image": self.analyze_image,
            "foreground_window": self.desktop.foreground_window,
            "clipboard_text": self.desktop.clipboard_text,
            "list_windows": self.desktop.list_windows,
            "open_app": self.open_app,
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
            "find_capability": self.find_capability,
            "list_capabilities": self.list_capabilities,
            "save_capability": self.save_capability,
            "set_capability_secret": self.set_capability_secret,
            "install_capability_package": self.install_capability_package,
            "use_capability": self.use_capability,
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

    def _alias_roots(self) -> dict[str, Path]:
        return {
            "桌面": self.desktop_dir,
            "desktop": self.desktop_dir,
            "资料库": self.library,
            "文件库": self.library,
            "库": self.library,
            "pailib": self.library,
            "音乐": self.library / "music",
            "music": self.library / "music",
            "图片": self.library / "pictures",
            "照片": self.library / "pictures",
            "pictures": self.library / "pictures",
            "视频": self.library / "videos",
            "电影": self.library / "videos",
            "录像": self.library / "videos",
            "videos": self.library / "videos",
            "文档": self.library / "files",
            "files": self.library / "files",
            "笔记": self.library / "files" / "notes",
            "备忘": self.library / "files" / "notes",
            "notes": self.library / "files" / "notes",
            "收件箱": self.library / "files" / "inbox",
            "未分类": self.library / "files" / "inbox",
            "浏览": self.library / "files" / "inbox",
            "文件": self.library / "files" / "inbox",
            "inbox": self.library / "files" / "inbox",
            "pdf": self.library / "files" / "pdf",
            "pdf目录": self.library / "files" / "pdf",
            "word目录": self.library / "files" / "word",
            "ppt目录": self.library / "files" / "slides",
            "表格": self.library / "files" / "sheets",
            "sheets": self.library / "files" / "sheets",
            "幻灯片": self.library / "files" / "slides",
            "slides": self.library / "files" / "slides",
            "工作区": self.workspace,
            "项目": self.workspace,
        }

    def _resolve_path(self, path: str) -> Path:
        """Resolve a user-supplied path against the workspace (legacy / code files)."""
        return self._resolve_named(path, default="workspace")

    def _resolve_named(self, path: str, *, default: str = "library") -> Path:
        """Resolve aliases. Relative library files go to the matching type subfolder."""
        raw = (path or "").strip().strip('"')
        if not raw:
            if default == "workspace":
                return self.workspace
            return self.library / "files" / "inbox"
        normalized = raw.replace("\\", "/")
        lowered = normalized.strip().lower()
        for key, target in sorted(self._alias_roots().items(), key=lambda item: -len(item[0])):
            key_l = key.replace("\\", "/").lower()
            if lowered == key_l:
                return target.resolve()
            prefix = key_l + "/"
            if lowered.startswith(prefix):
                rest = normalized[len(prefix) :]
                return (target / rest).resolve() if rest else target.resolve()
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        if default == "workspace":
            return (self.workspace / candidate).resolve()
        return (self.library / library_subdir_for(candidate.name) / candidate).resolve()

    def _is_alias_path(self, path: str) -> bool:
        lowered = (path or "").replace("\\", "/").strip().lower()
        if not lowered:
            return False
        for key in self._alias_roots():
            key_l = key.lower()
            if lowered == key_l or lowered.startswith(key_l + "/"):
                return True
        return False

    def _library_read_candidates(self, raw: str) -> list[Path]:
        rel = Path(raw)
        extras = [
            (self.library / library_subdir_for(rel.name) / rel).resolve(),
            (self.library / "files" / "inbox" / rel).resolve(),
            (self.library / "files" / "notes" / rel).resolve(),
            (self.library / "files" / rel).resolve(),
            (self.library / "pictures" / rel).resolve(),
            (self.library / "pictures" / "inbox" / rel).resolve(),
            (self.library / "music" / rel).resolve(),
            (self.library / "videos" / rel).resolve(),
            (self.library / rel).resolve(),
        ]
        seen: set[Path] = set()
        ordered: list[Path] = []
        for item in extras:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _resolve_for_read(self, path: str) -> Path:
        """Relative reads: existing file wins; else code → workspace, documents → type folder."""
        raw = (path or "").strip().strip('"')
        if not raw:
            return self.library / "files" / "inbox"
        if Path(raw).expanduser().is_absolute() or self._is_alias_path(raw):
            return self._resolve_named(raw, default="library")
        workspace_hit = (self.workspace / raw).resolve()
        lib_hits = self._library_read_candidates(raw)
        for item in [workspace_hit, *lib_hits]:
            if item.exists():
                return item
        if is_code_name(raw):
            return workspace_hit
        return lib_hits[0]

    def _inside(self, target: Path, root: Path) -> bool:
        try:
            target.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _inside_writable(self, target: Path) -> bool:
        return any(
            self._inside(target, root)
            for root in (self.workspace, self.library, self.desktop_dir)
        )

    def _inside_workspace(self, target: Path) -> bool:
        return self._inside(target, self.workspace)

    async def read_file(self, path: str) -> str:
        """Read a local text file (truncated to keep prompts small)."""
        target = self._resolve_for_read(path)
        if not target.exists():
            return f"文件不存在：{target}"
        if not target.is_file():
            return f"不是文件：{target}"
        data = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        if len(data) > 12000:
            data = data[:12000] + "\n…(truncated)"
        return data

    async def write_file(self, path: str, content: str = "") -> str:
        """Write text. Relative paths go to the matching PaiLib type folder."""
        target = self._resolve_named(path, default="library")
        if not self._inside_writable(target):
            return f"只能写到资料库、工作区或桌面：{target}"
        target.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> None:
            target.write_text(content or "", encoding="utf-8")

        await asyncio.to_thread(_write)
        return f"已写入：{target}"

    def _search_bases(self, glob_pat: str, root: str | None) -> list[tuple[str, Path]]:
        if root:
            return [("", self._resolve_named(root, default="library"))]
        if is_code_name(glob_pat):
            return [("工作区", self.workspace)]
        sub = default_browse_subdir(glob_pat)
        if not sub:
            return [("工作区", self.workspace)]
        return [("资料库", self.library / sub)]

    async def search_files(self, pattern: str, root: str | None = None) -> str:
        """Glob-search. No root: code → workspace; otherwise the matching library subfolder."""
        glob_pat = (pattern or "").strip() or "*"
        bases = self._search_bases(glob_pat, root)
        lines: list[str] = []
        for label, base in bases:
            if not base.exists():
                if root:
                    return f"搜索根目录不存在：{base}"
                continue
            search_pat = glob_pat
            if not root and search_pat.startswith("*.") and "/" not in search_pat.replace("\\", "/"):
                search_pat = "**/" + search_pat

            matches = await asyncio.to_thread(lambda b=base, p=search_pat: sorted(b.glob(p))[:50])
            for item in matches:
                try:
                    relative = item.relative_to(self.library if label == "资料库" else base)
                except ValueError:
                    try:
                        relative = item.relative_to(base)
                    except ValueError:
                        relative = item
                lines.append(f"[{label}] {relative}" if label else str(relative))
        if not lines and not root and not is_code_name(glob_pat):
            fallback = self.library
            search_pat = glob_pat if glob_pat.startswith("**") else f"**/{glob_pat.lstrip('./')}"
            matches = await asyncio.to_thread(lambda: sorted(fallback.glob(search_pat))[:50])
            for item in matches:
                try:
                    relative = item.relative_to(self.library)
                except ValueError:
                    relative = item
                lines.append(f"[资料库] {relative}")
        if not lines:
            return "没有匹配的文件。"
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
        target = self._resolve_for_read(path)
        if not target.exists():
            return f"图片不存在：{target}"
        return await self.vision.analyze(target)

    async def open_app(self, target: str) -> str:
        """Launch an app, or open a path with the Windows default program."""
        raw = (target or "").strip()
        if not raw:
            return await self.desktop.open_app(raw)
        if self._is_alias_path(raw) or Path(raw).expanduser().is_absolute():
            raw = str(self._resolve_named(raw, default="library"))
        elif Path(raw).suffix:
            located = self._resolve_for_read(raw)
            raw = str(located if located.exists() else self._resolve_named(raw, default="library"))
        return await self.desktop.open_app(raw)

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
        target = self._resolve_for_read(path)
        if not self._inside_writable(target):
            return f"只能删除资料库、工作区或桌面上的文件：{target}"
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

    def _user_said_save(self, confirm: Any) -> bool:
        from tools.capability import user_said_save

        if not _flag(confirm, False):
            return False
        return user_said_save(self.last_user_text or "")

    async def find_capability(self, need: str) -> str:
        from tools.capability import candidates_from_search, format_find_result
        from tools.websearch import search_web

        query = (need or "").strip()
        if not query:
            return "没有说明缺什么能力。"
        saved: list = []
        store = getattr(self.memory, "capabilities", None) if self.memory is not None else None
        if store is not None:
            saved = await store.find_matching(query)
        timeout = float(getattr(self.settings, "web_search_timeout", 8.0) or 8.0)
        proxy = (getattr(self.settings, "web_search_proxy", "") or "").strip() or None
        backend = (getattr(self.settings, "web_search_backend", "bing") or "bing").strip()
        search_q = f"{query} 官方 API 数据接口 申请"
        error = ""
        rows: list = []
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    search_web,
                    search_q,
                    max_results=6,
                    timeout=timeout,
                    proxy=proxy,
                    backend=backend,
                ),
                timeout=timeout + 2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("find_capability search failed: {}", exc)
            error = str(exc)
        candidates = candidates_from_search(rows or [])
        return format_find_result(query, saved=saved, candidates=candidates, search_error=error)

    async def list_capabilities(self) -> str:
        store = getattr(self.memory, "capabilities", None) if self.memory is not None else None
        if store is None:
            return "能力存储未就绪。"
        return store.format_list(await store.list_active())

    async def save_capability(
        self,
        name: str,
        source: str,
        status: str = "waiting_key",
        homepage: str = "",
        notes: str = "",
        confirm: bool = False,
        env_key: str = "",
    ) -> str:
        store = getattr(self.memory, "capabilities", None) if self.memory is not None else None
        if store is None:
            return "能力存储未就绪。"
        if not self._user_said_save(confirm):
            return "记下能力路需要你本轮明确说「记下」或「确认」。未确认不写入。"
        return await store.upsert(
            name, source, status=status, homepage=homepage, notes=notes, env_key=env_key
        )

    async def set_capability_secret(
        self,
        env_key: str,
        value: str,
        name: str = "",
        confirm: bool = False,
    ) -> str:
        if not self._user_said_save(confirm):
            return "写入密钥需要你本轮明确说「确认」或「记下」。密钥不会进记忆、不会打进日志。"
        from tools.envfile import upsert_env_value, validate_env_key

        try:
            key_name = validate_env_key(env_key)
            upsert_env_value(self.env_path, key_name, value)
        except ValueError as exc:
            return str(exc)
        store = getattr(self.memory, "capabilities", None) if self.memory is not None else None
        if store is not None and (name or "").strip():
            await store.upsert(
                name.strip(),
                source=key_name,
                status="ready",
                env_key=key_name,
                notes="密钥已写入 .env，不在对话里保存。",
            )
        return f"已写入 .env 的 {key_name}，当前进程可用。请勿把密钥再发给别人。未改程序本身。"

    async def install_capability_package(self, package: str, confirm: bool = False) -> str:
        from tools.capability import PIP_WHITELIST

        pkg = (package or "").strip().lower().replace("_", "-")
        if pkg == "ak-share":
            pkg = "akshare"
        if pkg not in PIP_WHITELIST:
            return (
                f"拒绝安装「{package}」。白名单只有：{', '.join(sorted(PIP_WHITELIST))}。"
                "其他包不能靠对话安装，避免把环境装坏。"
            )
        if not self._user_said_save(confirm):
            return f"安装 {pkg} 需要你本轮说「确认安装」。只装进当前 Python 环境。"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            pkg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        except TimeoutError:
            process.kill()
            return f"安装 {pkg} 超时。"
        if process.returncode != 0:
            err = (stderr or stdout).decode("utf-8", errors="replace")[-400:]
            return f"安装失败（未改小派源码）：{err}"
        return f"已在当前环境安装 {pkg}。可以说「600519 近半年收盘价」试日线。"

    async def use_capability(self, kind: str = "auto", query: str = "") -> str:
        from tools.runners import run_kind

        text = (query or "").strip() or (self.last_user_text or "")
        return await run_kind(kind, text)


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
