"""Windows desktop tools: foreground window, clipboard, open/focus apps."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

APP_ALIASES: dict[str, str] = {
    "记事本": "notepad.exe",
    "notepad": "notepad.exe",
    "计算器": "calc.exe",
    "calc": "calc.exe",
    "画图": "mspaint.exe",
    "画板": "mspaint.exe",
    "资源管理器": "explorer.exe",
    "文件管理器": "explorer.exe",
    "explorer": "explorer.exe",
    "浏览器": "msedge.exe",
    "edge": "msedge.exe",
    "微软浏览器": "msedge.exe",
    "chrome": "chrome.exe",
    "谷歌浏览器": "chrome.exe",
    "vscode": "code",
    "vs code": "code",
    "cursor": "cursor",
    "终端": "wt.exe",
    "windows terminal": "wt.exe",
    "设置": "ms-settings:",
    "任务管理器": "taskmgr.exe",
}

_UNSAFE_SNIPPETS = (
    "shutdown",
    "format ",
    "del /",
    "rmdir",
    "rd /s",
    "reg delete",
    "powershell -enc",
    "powershell -e ",
    "cmd /c",
    "&&",
    "|",
    ">",
    "<",
    "`",
    "$(",
)


def resolve_app_target(target: str) -> str:
    """Map a spoken/typed app name to an executable, URL, or path."""
    raw = (target or "").strip().strip('"')
    if not raw:
        return ""
    key = raw.lower()
    if raw in APP_ALIASES:
        return APP_ALIASES[raw]
    if key in APP_ALIASES:
        return APP_ALIASES[key]
    return raw


def is_unsafe_launch(target: str) -> bool:
    """Reject shell metacharacters and destructive system commands."""
    lowered = (target or "").strip().lower()
    if not lowered:
        return True
    return any(token in lowered for token in _UNSAFE_SNIPPETS)


def _looks_like_url(target: str) -> bool:
    parsed = urlparse(target)
    return parsed.scheme in {"http", "https", "ms-settings", "mailto"}


class DesktopTools:
    """Async wrappers around Win32 window/clipboard/process helpers."""

    async def foreground_window(self) -> str:
        """Describe the window currently in the foreground."""
        return await asyncio.to_thread(_foreground_window_sync)

    async def clipboard_text(self) -> str:
        """Return Unicode text from the clipboard, if any."""
        return await asyncio.to_thread(_clipboard_text_sync)

    async def list_windows(self, query: str = "") -> str:
        """List visible top-level window titles, optionally filtered."""
        return await asyncio.to_thread(_list_windows_sync, query)

    async def open_app(self, target: str) -> str:
        """Launch an app alias, executable, file, folder, or http(s) URL."""
        resolved = resolve_app_target(target)
        if not resolved:
            return "没有指定要打开的应用或路径。"
        if is_unsafe_launch(resolved):
            return f"拒绝打开：目标看起来不安全（{target}）。"
        return await asyncio.to_thread(_open_target_sync, resolved)

    async def focus_window(self, title: str) -> str:
        """Bring a visible window whose title contains `title` to the front."""
        needle = (title or "").strip()
        if not needle:
            return "没有指定窗口标题。"
        return await asyncio.to_thread(_focus_window_sync, needle)


def _foreground_window_sync() -> str:
    if sys.platform != "win32":
        return "当前系统不是 Windows，无法读取前台窗口。"
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "没有前台窗口。"
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value or "(无标题)"
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _process_image(int(pid.value)) if pid.value else ""
    return f"标题：{title}\n进程：{pid.value}\n程序：{exe or '未知'}"


def _foreground_bits() -> tuple[str, str]:
    if sys.platform != "win32":
        return "", ""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _process_image(int(pid.value)) if pid.value else ""
    return buf.value or "", exe


def foreground_is_meeting() -> bool:
    """True when the foreground window looks like Zoom/Teams/腾讯会议 etc."""
    from memory.protocols import looks_like_meeting

    title, exe = _foreground_bits()
    return looks_like_meeting(title, exe)


def _clipboard_text_sync() -> str:
    if sys.platform != "win32":
        return "当前系统不是 Windows，无法读取剪贴板。"
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    cf_unicode = 13
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    if not user32.OpenClipboard(None):
        return "无法打开剪贴板（可能被其他程序占用）。"
    try:
        handle = user32.GetClipboardData(cf_unicode)
        if not handle:
            return "剪贴板为空，或不是文字。"
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return "剪贴板锁定失败。"
        try:
            text = ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
    text = (text or "").strip()
    if not text:
        return "剪贴板为空，或不是文字。"
    if len(text) > 4000:
        return text[:4000] + "\n…(truncated)"
    return text


def _list_windows_sync(query: str) -> str:
    if sys.platform != "win32":
        return "当前系统不是 Windows。"
    windows = _enum_windows()
    needle = (query or "").strip().lower()
    if needle:
        windows = [item for item in windows if needle in item["title"].lower()]
    if not windows:
        return "没有匹配的可见窗口。"
    lines = [f"- {item['title']}" for item in windows[:30]]
    return f"可见窗口 {min(len(windows), 30)} 个：\n" + "\n".join(lines)


def _open_target_sync(target: str) -> str:
    if _looks_like_url(target) or target.startswith("ms-settings:"):
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
            return f"已打开：{target}"
        return f"无法在此系统打开 URL：{target}"
    path = Path(target).expanduser()
    if path.exists():
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
            return f"已打开：{path}"
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603
        return f"已打开：{path}"
    found = shutil.which(target) or shutil.which(target + ".exe")
    if found:
        subprocess.Popen([found], close_fds=True)  # noqa: S603
        return f"已启动：{found}"
    if sys.platform == "win32" and target.lower().endswith(".exe"):
        try:
            subprocess.Popen(target, shell=False, close_fds=True)  # noqa: S603
            return f"已尝试启动：{target}"
        except OSError as exc:
            return f"启动失败：{target}（{exc}）"
    logger.info("open_app miss {}", target)
    return f"找不到应用或路径：{target}。可以给可执行文件名、别名（记事本/计算器）或完整路径。"


def _focus_window_sync(title: str) -> str:
    if sys.platform != "win32":
        return "当前系统不是 Windows。"
    import ctypes

    user32 = ctypes.windll.user32
    needle = title.lower()
    match = next((item for item in _enum_windows() if needle in item["title"].lower()), None)
    if match is None:
        return f"没有找到标题包含「{title}」的窗口。"
    hwnd = match["hwnd"]
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    return f"已切换到：{match['title']}"


def _enum_windows() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    results: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value.strip()
        if not title:
            return True
        results.append({"hwnd": hwnd, "title": title})
        return True

    user32.EnumWindows(_callback, 0)
    return results


def _process_image(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    access = 0x1000
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        query = getattr(kernel32, "QueryFullProcessImageNameW", None)
        if query is None:
            return ""
        if query(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)
