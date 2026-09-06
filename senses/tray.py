"""System-tray presence and Windows Startup shortcut."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from senses.presence import STATUS_META, make_status_icon, status_label


def startup_shortcut_path() -> Path:
    """Return the .lnk path used for auto-start on logon."""
    appdata = os.environ.get("APPDATA") or ""
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "PersonalAI.lnk"
    )


def is_autostart_enabled() -> bool:
    """True when the Startup shortcut exists (including a leftover 织-named link)."""
    return startup_shortcut_path().is_file() or _legacy_startup_path().is_file()


def _legacy_startup_path() -> Path:
    return startup_shortcut_path().with_name("PersonalAI-织.lnk")


def set_autostart(enabled: bool) -> str:
    """Create or remove a Startup shortcut to run.bat."""
    link = startup_shortcut_path()
    legacy = _legacy_startup_path()
    if not enabled:
        errors = []
        for path in (link, legacy):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(str(exc))
        if errors:
            return f"无法删除开机启动项：{'; '.join(errors)}"
        return "已关闭开机启动。"
    bat = PROJECT_ROOT / "run.bat"
    if not bat.is_file():
        return f"找不到 {bat}"
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(link))}'); "
        f"$s.TargetPath = '{_ps_escape(str(bat))}'; "
        f"$s.WorkingDirectory = '{_ps_escape(str(PROJECT_ROOT))}'; "
        "$s.WindowStyle = 1; "
        "$s.Description = 'Personal AI OS'; "
        "$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"无法创建开机启动项：{exc}"
    return f"已开启开机启动：{link}"


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")


def _make_icon_image(status: str = "idle") -> Any:
    """Backward-compatible alias used by tests if they import this helper."""
    return make_status_icon(status)


def _console_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(__import__("ctypes").windll.kernel32.GetConsoleWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def show_console(visible: bool) -> None:
    """Show or hide the attached console window."""
    hwnd = _console_hwnd()
    if not hwnd:
        return
    try:
        __import__("ctypes").windll.user32.ShowWindow(hwnd, 5 if visible else 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ShowWindow: {}", exc)


class TrayPresence:
    """pystray icon: listen, hide console, autostart, quit."""

    def __init__(
        self,
        incoming: queue.Queue[tuple[str, str]],
        stop: threading.Event,
        *,
        name: str = "小派",
        codename: str = "Pai",
    ) -> None:
        self.incoming = incoming
        self.stop = stop
        self.name = name
        self.codename = codename
        self._icon: Any = None
        self._console_visible = True
        self._status = "idle"

    def set_status(self, status: str) -> None:
        """Recolor the tray disc and tooltip to match presence."""
        self._status = status if status in STATUS_META else "idle"
        icon = self._icon
        if icon is None:
            return
        title = f"{self.name} · {status_label(self._status)}"
        try:
            icon.title = title
            icon.icon = make_status_icon(self._status)
            if hasattr(icon, "update_menu"):
                icon.update_menu()
        except Exception as exc:  # noqa: BLE001
            logger.debug("tray icon update: {}", exc)

    def start(self) -> None:
        """Run the tray icon on a daemon thread."""
        threading.Thread(target=self._run, daemon=True, name="tray").start()

    def stop_icon(self) -> None:
        """Ask pystray to exit."""
        icon = self._icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def notify(self, title: str, message: str) -> None:
        """Best-effort balloon notification."""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(message, title)
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:
        try:
            import pystray
            from pystray import MenuItem
        except Exception as exc:  # noqa: BLE001
            logger.warning("pystray unavailable, tray disabled: {}", exc)
            return

        def listen(_icon: Any, _item: Any) -> None:
            self.incoming.put(("tray", "/listen"))

        def toggle_console(_icon: Any, _item: Any) -> None:
            self._console_visible = not self._console_visible
            show_console(self._console_visible)

        def toggle_autostart(_icon: Any, _item: Any) -> None:
            message = set_autostart(not is_autostart_enabled())
            logger.info("{}", message)
            self.notify(self.name, message)

        def quit_app(_icon: Any, _item: Any) -> None:
            self.incoming.put(("tray", "/quit"))
            self.stop.set()
            _icon.stop()

        def _noop(_icon: Any, _item: Any) -> None:
            return

        menu = pystray.Menu(
            MenuItem(lambda _: f"状态：{status_label(self._status)}", _noop, enabled=False),
            MenuItem("开始听", listen, default=True),
            MenuItem(
                "显示控制台",
                toggle_console,
                checked=lambda _: self._console_visible,
            ),
            MenuItem(
                "开机启动",
                toggle_autostart,
                checked=lambda _: is_autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            MenuItem("退出", quit_app),
        )
        self._icon = pystray.Icon(
            self.name,
            make_status_icon("idle"),
            f"{self.name} · {status_label('idle')}",
            menu,
        )
        logger.info("Tray icon ready")
        try:
            self._icon.run()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tray exited: {}", exc)
