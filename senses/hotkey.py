"""Global hotkey via RegisterHotKey, with GetAsyncKeyState fallback."""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any

from loguru import logger

_VK_KEYS = {
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "L": 0x4C,
    "M": 0x4D,
    "K": 0x4B,
    "SPACE": 0x20,
    "SPACEBAR": 0x20,
}
_VK_MODS = {
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "MENU": 0x12,
    "WIN": 0x5B,
}
_MOD_CTRL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_ALT = 0x0001
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_HOTKEY_ID = 0xA101


def parse_hotkey(spec: str) -> tuple[list[int], int] | None:
    """Parse 'CTRL+SHIFT+L' into modifier VKs and the main key VK."""
    parts = [p.strip().upper() for p in (spec or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    main = parts[-1]
    mods = parts[:-1]
    main_vk = _VK_KEYS.get(main)
    if main_vk is None and len(main) == 1 and "A" <= main <= "Z":
        main_vk = ord(main)
    if main_vk is None:
        return None
    mod_vks: list[int] = []
    for mod in mods:
        vk = _VK_MODS.get(mod)
        if vk is None:
            return None
        mod_vks.append(vk)
    return mod_vks, main_vk


def _modifier_flags(mod_vks: list[int]) -> int:
    flags = _MOD_NOREPEAT
    for vk in mod_vks:
        if vk == 0x11:
            flags |= _MOD_CTRL
        elif vk == 0x10:
            flags |= _MOD_SHIFT
        elif vk == 0x12:
            flags |= _MOD_ALT
        elif vk == 0x5B:
            flags |= _MOD_WIN
    return flags


def start_hotkey_thread(
    incoming: queue.Queue[tuple[str, str]],
    stop: threading.Event,
    hotkey: str,
) -> None:
    """Register a system hotkey; fall back to polling if registration fails."""
    parsed = parse_hotkey(hotkey)
    if parsed is None or sys.platform != "win32":
        logger.warning("Hotkey '{}' not usable on this platform", hotkey)
        return
    mod_vks, main_vk = parsed
    if _register_loop(incoming, stop, hotkey, mod_vks, main_vk):
        return
    logger.warning("RegisterHotKey failed for {}, using fallback poll", hotkey)
    _poll_loop(incoming, stop, mod_vks, main_vk)


def _register_loop(
    incoming: queue.Queue[tuple[str, str]],
    stop: threading.Event,
    hotkey: str,
    mod_vks: list[int],
    main_vk: int,
) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        flags = _modifier_flags(mod_vks)
        if not user32.RegisterHotKey(None, _HOTKEY_ID, flags, main_vk):
            return False
        logger.info("Global hotkey registered {}", hotkey)
        msg = wintypes.MSG()
        while not stop.is_set():
            got = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if got:
                if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    incoming.put(("hotkey", "/listen"))
                elif msg.message == _WM_QUIT:
                    break
            else:
                time.sleep(0.05)
        user32.UnregisterHotKey(None, _HOTKEY_ID)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("RegisterHotKey unavailable: {}", exc)
        return False


def _poll_loop(
    incoming: queue.Queue[tuple[str, str]],
    stop: threading.Event,
    mod_vks: list[int],
    main_vk: int,
) -> None:
    try:
        user32 = __import__("ctypes").windll.user32
    except Exception:  # noqa: BLE001
        return
    last = 0.0
    while not stop.is_set():
        try:
            mods_down = all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in mod_vks) if mod_vks else True
            main_down = bool(user32.GetAsyncKeyState(main_vk) & 0x8000)
        except Exception:  # noqa: BLE001
            return
        now = time.monotonic()
        if mods_down and main_down and (now - last) > 0.7:
            last = now
            incoming.put(("hotkey", "/listen"))
        time.sleep(0.05)
