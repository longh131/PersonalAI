"""Laptop ship-status: battery, disk, network. No extra packages."""

from __future__ import annotations

import shutil
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SystemSnapshot:
    """One reading of battery / disk / network."""

    battery: int | None
    plugged: bool | None
    disk_free_gb: float
    disk_path: str
    online: bool


def read_system_snapshot() -> SystemSnapshot:
    """Collect a cheap local snapshot. Battery is Windows-only."""
    battery, plugged = _battery()
    disk_path, free_gb = _disk_free()
    return SystemSnapshot(
        battery=battery,
        plugged=plugged,
        disk_free_gb=free_gb,
        disk_path=disk_path,
        online=_online(),
    )


def format_status(snap: SystemSnapshot) -> str:
    """Short Chinese briefing for the system_status tool."""
    if snap.battery is None:
        bat = "电量：未知（台式机或读不到电池）"
    else:
        plug = "充电中" if snap.plugged else "未插电"
        bat = f"电量：{snap.battery}%（{plug}）"
    net = "网络：通" if snap.online else "网络：不通"
    disk = f"磁盘 {snap.disk_path} 剩余 {snap.disk_free_gb:.1f} GB"
    return f"{bat}\n{net}\n{disk}"


def _battery() -> tuple[int | None, bool | None]:
    if sys.platform != "win32":
        return None, None
    import ctypes

    class _Status(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_byte),
            ("BatteryFlag", ctypes.c_byte),
            ("BatteryLifePercent", ctypes.c_byte),
            ("SystemStatusFlag", ctypes.c_byte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    status = _Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None, None
    percent = int(status.BatteryLifePercent)
    if percent > 100:
        return None, None
    plugged: bool | None
    if status.ACLineStatus == 1:
        plugged = True
    elif status.ACLineStatus == 0:
        plugged = False
    else:
        plugged = None
    return percent, plugged


def _disk_free() -> tuple[str, float]:
    root = Path("C:\\") if sys.platform == "win32" else Path("/")
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        usage = shutil.disk_usage(".")
        root = Path(".")
    return str(root), usage.free / (1024**3)


def _online() -> bool:
    for host in ("1.1.1.1", "223.5.5.5"):
        try:
            socket.create_connection((host, 53), timeout=1.2).close()
            return True
        except OSError:
            continue
    return False


class HealthWatch:
    """Turn snapshots into rare spoken alerts. Cooldown per alert key."""

    def __init__(self, *, cooldown_s: float = 1800.0) -> None:
        self.cooldown_s = cooldown_s
        self._last_mono: dict[str, float] = {}
        self._was_online = True

    def alerts(self, snap: SystemSnapshot, *, now: float | None = None) -> list[str]:
        """Return new anomaly sentences, or empty when nothing should be said."""
        stamp = time.monotonic() if now is None else now
        messages: list[str] = []
        if snap.battery is not None and snap.plugged is False:
            if snap.battery <= 5 and self._ready("battery_critical", stamp):
                messages.append(f"电量 {snap.battery}%，很快要关机。")
            elif snap.battery <= 15 and self._ready("battery_low", stamp):
                messages.append(f"电量 {snap.battery}%，请充电。")
        if snap.disk_free_gb < 5 and self._ready("disk", stamp):
            messages.append(f"{snap.disk_path} 只剩 {snap.disk_free_gb:.1f} GB。")
        if self._was_online and not snap.online and self._ready("net_down", stamp):
            messages.append("网络断了。")
        if snap.online and not self._was_online:
            self._last_mono.pop("net_down", None)
        self._was_online = snap.online
        return messages

    def _ready(self, key: str, now: float) -> bool:
        previous = self._last_mono.get(key)
        if previous is not None and now - previous < self.cooldown_s:
            return False
        self._last_mono[key] = now
        return True
