"""JSON schemas for built-in tools (OpenAI function-calling format)."""

from __future__ import annotations

from typing import Any


def openai_tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build one OpenAI-style tool definition."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    openai_tool(
        "read_file",
        "读取本地文本文件内容。",
        {"path": {"type": "string", "description": "文件路径，相对工作区或绝对路径。"}},
        ["path"],
    ),
    openai_tool(
        "search_files",
        "按 glob 模式在工作区内搜索文件。",
        {
            "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
            "root": {"type": "string", "description": "可选搜索根目录。"},
        },
        ["pattern"],
    ),
    openai_tool(
        "web_search",
        "在互联网上搜索公开资料，返回标题、链接和摘要。",
        {
            "query": {"type": "string", "description": "搜索查询"},
            "max_results": {"type": "integer", "description": "返回条数，默认 5"},
        },
        ["query"],
    ),
    openai_tool(
        "execute_code",
        "在隔离临时目录中执行短 Python 代码，仅用于计算和文本处理。",
        {"code": {"type": "string", "description": "要执行的 Python 源码"}},
        ["code"],
    ),
    openai_tool(
        "capture_screen",
        "截取当前屏幕并做 OCR / 图像分析，适合用户说「看看屏幕」。",
        {"monitor": {"type": "integer", "description": "显示器序号，默认 1"}},
        [],
    ),
    openai_tool(
        "analyze_image",
        "读取一张本地图片，进行 OCR 并描述可见内容。",
        {"path": {"type": "string", "description": "图片路径"}},
        ["path"],
    ),
    openai_tool(
        "foreground_window",
        "查看用户此刻最前面的窗口标题和程序，适合「我在看什么」「当前窗口」。",
        {},
        [],
    ),
    openai_tool(
        "clipboard_text",
        "读取用户剪贴板里的文字，适合「我刚复制的」「剪贴板里是什么」。",
        {},
        [],
    ),
    openai_tool(
        "list_windows",
        "列出当前可见窗口标题，可用 query 过滤。",
        {"query": {"type": "string", "description": "可选，标题关键词。"}},
        [],
    ),
    openai_tool(
        "open_app",
        "打开应用、文件、文件夹或网页。别名包括记事本、计算器、浏览器、资源管理器、Cursor、VS Code。用户说「打开XX」时用这个。",
        {"target": {"type": "string", "description": "应用名、别名、可执行文件、路径或 http(s) URL。"}},
        ["target"],
    ),
    openai_tool(
        "focus_window",
        "把标题包含指定文字的窗口切到前台。用户说「切到XX」「回到XX窗口」时用。",
        {"title": {"type": "string", "description": "窗口标题的一部分。"}},
        ["title"],
    ),
    openai_tool(
        "calendar_agenda",
        "只读查看今天或未来几天的 Outlook 日历和未完成待办。用户问「今天有什么安排」「下午有没有会」「待办」时用。",
        {
            "days": {"type": "integer", "description": "从今天起看几天，默认 1，最多 14。"},
            "include_tasks": {"type": "boolean", "description": "是否包含未完成任务，默认 true。"},
        },
        [],
    ),
    openai_tool(
        "set_reminder",
        "在本机设定到点提醒，到时会开口说出内容。用户说「十分钟后提醒我」「三点叫我」时用。不要用这个发邮件。",
        {
            "message": {"type": "string", "description": "到点要说的内容。"},
            "delay_minutes": {"type": "number", "description": "多少分钟后提醒。与 at_local 二选一。"},
            "at_local": {
                "type": "string",
                "description": "本地时刻，如 15:30 或 2026-09-02 15:30。若已过则顺延到明天。",
            },
        },
        ["message"],
    ),
    openai_tool(
        "list_reminders",
        "列出尚未触发的本机提醒；传入 cancel_id 则取消那一条。",
        {"cancel_id": {"type": "integer", "description": "要取消的提醒编号。"}},
        [],
    ),
    openai_tool(
        "system_status",
        "查看本机舰况：电量、是否插电、磁盘剩余、网络是否通。用户问「电量多少」「磁盘还剩多少」时用。",
        {},
        [],
    ),
    openai_tool(
        "forget_memory",
        "忘掉长期记忆。用户说「忘掉那次行程」「别再记这个」时用。可按编号或关键词。",
        {
            "query": {"type": "string", "description": "要忘掉的内容关键词。"},
            "memory_id": {"type": "integer", "description": "已知的记忆编号。"},
        },
        [],
    ),
    openai_tool(
        "correct_memory",
        "纠正一条长期记忆：先忘掉旧的，再记下新的。用户说「以后别这么叫我，叫我X」时用。",
        {
            "query": {"type": "string", "description": "要替换掉的旧内容。"},
            "replacement": {"type": "string", "description": "正确内容。"},
        },
        ["query", "replacement"],
    ),
    openai_tool(
        "upsert_entity",
        "记下人物、项目或稳定偏好。用户介绍「老板是张三」「主项目是PersonalAI」「我不喜欢长邮件」时用。",
        {
            "kind": {"type": "string", "description": "person / project / preference，或中文：人物、项目、偏好。"},
            "name": {"type": "string", "description": "姓名、项目名或偏好短名。"},
            "relation": {"type": "string", "description": "关系，如老板、主项目。"},
            "notes": {"type": "string", "description": "补充说明。"},
        },
        ["kind", "name"],
    ),
    openai_tool(
        "list_entities",
        "列出已记下的人物、项目、偏好。",
        {"kind": {"type": "string", "description": "可选过滤：人物 / 项目 / 偏好。"}},
        [],
    ),
    openai_tool(
        "forget_entity",
        "忘掉某个人物、项目或偏好。",
        {
            "name": {"type": "string", "description": "名字。"},
            "kind": {"type": "string", "description": "可选类型。"},
        },
        ["name"],
    ),
    openai_tool(
        "set_protocol",
        "开关说话协议。会议中不出声、夜间只提醒、完全静音。用户说「会议中不要出声」「晚上十点后只提醒」「取消静音」时用。",
        {
            "name": {
                "type": "string",
                "description": "mute / meeting_mute / quiet_hours，或中文：静音、会议中不出声、夜间只提醒。",
            },
            "enabled": {"type": "boolean", "description": "true 开启，false 关闭。"},
            "start": {"type": "string", "description": "夜间开始，如 22:00。"},
            "end": {"type": "string", "description": "夜间结束，如 08:00。"},
        },
        ["name"],
    ),
    openai_tool(
        "list_protocols",
        "查看当前静音 / 会议 / 夜间协议。",
        {},
        [],
    ),
    openai_tool(
        "daily_briefing",
        "拼一段很短的晨间或离开简报：日程、未完成提醒、异常舰况。用户说「今天简报」「早报」「我要走了」「下班了」时用。Outlook 读不到就跳过日程。",
        {
            "kind": {
                "type": "string",
                "description": "morning / leaving / auto。早报用 morning，离开或下班用 leaving，说不准用 auto。",
            },
        },
        [],
    ),
    openai_tool(
        "background_task",
        "把耗时工作丢到后台，立刻告诉用户先去做，不要在本轮自己跑完。适合长检索、写长文件、多步调研。用户还可以继续说话。",
        {"instruction": {"type": "string", "description": "后台要独立完成的完整指令。"}},
        ["instruction"],
    ),
    openai_tool(
        "set_volume",
        "调节系统音量。用户说「音量调大 / 调小 / 静音」时用 action；精确百分比用 percent（本机可能不支持）。",
        {
            "action": {"type": "string", "description": "up / down / mute，或中文：调大、调小、静音。"},
            "percent": {"type": "integer", "description": "0–100。能设则设，不能则请用户改说调大调小。"},
        },
        [],
    ),
    openai_tool(
        "set_dnd",
        "开关勿扰：开启后助手不再出声（mute 协议），不是系统 Focus Assist。用户说「勿扰」「别出声」「取消勿扰」时用。",
        {"enabled": {"type": "boolean", "description": "true 开启勿扰（静音），false 关闭。"}},
        [],
    ),
    openai_tool(
        "lock_pc",
        "立刻锁定 Windows 工作站。用户说「锁屏」「锁定电脑」时用，不必再确认。",
        {},
        [],
    ),
    openai_tool(
        "delete_file",
        "删除工作区内的一个文件（不能删目录、不能删工作区外）。必须 confirm=true，且用户本轮明确说了「确认」。",
        {
            "path": {"type": "string", "description": "相对工作区或绝对路径。"},
            "confirm": {"type": "boolean", "description": "仅当用户本轮说了确认时为 true。"},
        },
        ["path"],
    ),
    openai_tool(
        "power_action",
        "关机、重启或睡眠。必须 confirm=true，且用户本轮明确说了「确认」。锁定电脑请用 lock_pc，不要用这个。",
        {
            "action": {"type": "string", "description": "shutdown / restart / sleep，或中文：关机、重启、睡眠。"},
            "confirm": {"type": "boolean", "description": "仅当用户本轮说了确认时为 true。"},
        },
        ["action"],
    ),
]


def schema_by_name() -> dict[str, dict[str, Any]]:
    """Index tool schemas by function name."""
    return {item["function"]["name"]: item for item in TOOL_SCHEMAS}
