"""Read-only calendar and tasks from classic Outlook via PowerShell COM."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

_OUTLOOK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::UTF8
$days = [int]$env:PAI_CAL_DAYS
if ($days -lt 1) { $days = 1 }
if ($days -gt 14) { $days = 14 }
$includeTasks = $env:PAI_CAL_TASKS -eq '1'
$outlook = New-Object -ComObject Outlook.Application
$ns = $outlook.GetNamespace('MAPI')
$start = [datetime]::Today
$end = $start.AddDays($days)
$cal = $ns.GetDefaultFolder(9)
$items = $cal.Items
$items.IncludeRecurrences = $true
$items.Sort('[Start]')
$filter = "[Start] >= '" + $start.ToString('g') + "' AND [Start] < '" + $end.ToString('g') + "'"
$restricted = $items.Restrict($filter)
$events = New-Object System.Collections.Generic.List[object]
$count = 0
foreach ($it in $restricted) {
    if ($count -ge 40) { break }
    try {
        $events.Add([pscustomobject]@{
            kind = 'event'
            title = [string]$it.Subject
            start = ([datetime]$it.Start).ToString('o')
            end = ([datetime]$it.End).ToString('o')
            location = [string]$it.Location
            all_day = [bool]$it.AllDayEvent
        })
        $count++
    } catch {}
}
$tasks = New-Object System.Collections.Generic.List[object]
if ($includeTasks) {
    $folder = $ns.GetDefaultFolder(13)
    $tcount = 0
    foreach ($it in $folder.Items) {
        if ($tcount -ge 20) { break }
        try {
            if ($it.Complete) { continue }
            $due = $null
            try { if ($it.DueDate -and $it.DueDate.Year -gt 1980) { $due = ([datetime]$it.DueDate).ToString('o') } } catch {}
            $tasks.Add([pscustomobject]@{
                kind = 'task'
                title = [string]$it.Subject
                due = $due
            })
            $tcount++
        } catch {}
    }
}
$result = [pscustomobject]@{ events = $events; tasks = $tasks }
$result | ConvertTo-Json -Compress -Depth 4
"""


def format_agenda(events: list[dict[str, Any]], tasks: list[dict[str, Any]], *, days: int) -> str:
    """Turn structured Outlook rows into a short Chinese briefing."""
    lines: list[str] = []
    if days <= 1:
        heading = "今天的日程"
    else:
        heading = f"未来 {days} 天的日程"
    if events:
        lines.append(heading + "：")
        for item in events:
            title = str(item.get("title") or "（无标题）").strip() or "（无标题）"
            when = _format_when(item.get("start"), item.get("end"), bool(item.get("all_day")))
            location = str(item.get("location") or "").strip()
            extra = f" · {location}" if location else ""
            lines.append(f"- {when} {title}{extra}")
    else:
        lines.append(heading + "：没有事件。")
    open_tasks = [item for item in tasks if str(item.get("title") or "").strip()]
    if open_tasks:
        lines.append("未完成待办：")
        for item in open_tasks[:15]:
            due = _format_due(item.get("due"))
            suffix = f"（截止 {due}）" if due else ""
            lines.append(f"- {item['title']}{suffix}")
    return "\n".join(lines)


def _format_when(start: Any, end: Any, all_day: bool) -> str:
    start_dt = _parse_iso(start)
    if start_dt is None:
        return "时间未知"
    local = start_dt.astimezone()
    if all_day:
        return local.strftime("%m-%d 全天")
    stamp = local.strftime("%m-%d %H:%M")
    end_dt = _parse_iso(end)
    if end_dt is None:
        return stamp
    end_local = end_dt.astimezone()
    if end_local.date() == local.date():
        return f"{stamp}–{end_local.strftime('%H:%M')}"
    return f"{stamp}–{end_local.strftime('%m-%d %H:%M')}"


def _format_due(due: Any) -> str:
    stamp = _parse_iso(due)
    if stamp is None:
        return ""
    return stamp.astimezone().strftime("%m-%d")


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


async def fetch_outlook_agenda(*, days: int = 1, include_tasks: bool = True) -> str:
    """Read classic Outlook calendar (and optional tasks) via COM. Read-only."""
    span = max(1, min(int(days or 1), 14))
    try:
        payload = await _run_outlook(span, include_tasks)
    except FileNotFoundError:
        return "本机没有 PowerShell，读不了 Outlook 日历。"
    except subprocess.TimeoutExpired:
        return "读取 Outlook 超时。请确认经典 Outlook 已登录且未弹出对话框。"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Outlook calendar failed: {}", exc)
        return (
            "读不到系统日历。当前只支持已安装并登录的「经典 Outlook」。"
            f"细节：{exc}"
        )
    if payload.get("error"):
        return (
            "读不到系统日历。当前只支持已安装并登录的「经典 Outlook」（不是新版 Outlook 网页壳）。"
            f"细节：{payload['error']}"
        )
    events = list(payload.get("events") or [])
    tasks = list(payload.get("tasks") or []) if include_tasks else []
    if not events and not tasks:
        extra = "（已连接 Outlook，这段时间没有事件或待办。）"
        return format_agenda([], [], days=span) + extra
    return format_agenda(events, tasks, days=span)


async def _run_outlook(days: int, include_tasks: bool) -> dict[str, Any]:
    """Run the COM script and parse JSON. Returns {'error': ...} on failure."""
    import asyncio
    import os

    env = os.environ.copy()
    env["PAI_CAL_DAYS"] = str(days)
    env["PAI_CAL_TASKS"] = "1" if include_tasks else "0"
    proc = await asyncio.to_thread(
        subprocess.run,
        ["powershell", "-NoProfile", "-Command", _OUTLOOK_SCRIPT],
        capture_output=True,
        timeout=25,
        env=env,
        check=False,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not stdout:
        detail = stderr or stdout or f"exit {proc.returncode}"
        return {"error": detail[:400]}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": stdout[:400]}
    if not isinstance(data, dict):
        return {"error": "Outlook 返回格式异常"}
    events = data.get("events")
    tasks = data.get("tasks")
    if events is None:
        events = []
    if tasks is None:
        tasks = []
    if isinstance(events, dict):
        events = [events]
    if isinstance(tasks, dict):
        tasks = [tasks]
    return {"events": events, "tasks": tasks}


def near_term_hint(events: list[dict[str, Any]], *, minutes: int = 15) -> str:
    """Optional one-liner if something starts soon. Unused by tools; kept for tests."""
    horizon = datetime.now().astimezone() + timedelta(minutes=minutes)
    for item in events:
        start = _parse_iso(item.get("start"))
        if start is None or bool(item.get("all_day")):
            continue
        local = start.astimezone()
        if datetime.now().astimezone() <= local <= horizon:
            title = str(item.get("title") or "日程")
            return f"{local.strftime('%H:%M')} 有「{title}」"
    return ""
