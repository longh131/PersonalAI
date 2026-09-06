"""Volume media keys, optional pycaw percent, lock and confirmed power actions."""

from __future__ import annotations

import ctypes
import subprocess
import sys

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002


def _key(vk: int) -> None:
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def volume_nudge(direction: str) -> str:
    """Relative volume via media keys. direction is up/down/mute/toggle."""
    if sys.platform != "win32":
        return "当前系统不是 Windows。"
    action = (direction or "").strip().lower()
    if action in {"mute", "unmute", "toggle", "静音", "取消静音", "切换静音"}:
        _key(VK_VOLUME_MUTE)
        return "已切换静音键。"
    if action in {"up", "大", "大声", "调大"}:
        for _ in range(4):
            _key(VK_VOLUME_UP)
        return "音量已调大。"
    if action in {"down", "小", "小声", "调小"}:
        for _ in range(4):
            _key(VK_VOLUME_DOWN)
        return "音量已调小。"
    return f"不支持的音量动作：{direction}"


def set_volume_percent(percent: int) -> str:
    """Set output volume 0–100. Uses pycaw when installed; otherwise explains the fallback."""
    if sys.platform != "win32":
        return "当前系统不是 Windows。"
    level = max(0, min(100, int(percent)))
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"音量已设为 {level}%。"
    except Exception:
        if level <= 0:
            return volume_nudge("mute")
        return "精确百分比需要本机 pycaw。请说「音量调大」或「音量调小」。"


def lock_workstation() -> str:
    if sys.platform != "win32":
        return "当前系统不是 Windows。"
    if ctypes.windll.user32.LockWorkStation():
        return "已锁定工作站。"
    return "锁定失败。"


def sleep_or_power(action: str) -> str:
    """sleep / shutdown / restart. Caller must already have confirmed."""
    if sys.platform != "win32":
        return "当前系统不是 Windows。"
    wanted = (action or "").strip().lower()
    if wanted in {"sleep", "睡眠", "休眠"}:
        try:
            ctypes.windll.powrprof.SetSuspendState(False, True, False)
            return "正在睡眠。"
        except Exception as exc:  # noqa: BLE001
            return f"睡眠失败：{exc}"
    if wanted in {"shutdown", "关机"}:
        subprocess.run(["shutdown", "/s", "/t", "0"], check=False, creationflags=0x08000000)
        return "正在关机。"
    if wanted in {"restart", "重启"}:
        subprocess.run(["shutdown", "/r", "/t", "0"], check=False, creationflags=0x08000000)
        return "正在重启。"
    return f"不支持的电源动作：{action}"
