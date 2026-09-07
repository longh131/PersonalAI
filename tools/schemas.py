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
        "读取本地文本文件。没指定目录时先找已存在的工作区/资料库文件；新建位置按类型进 notes/pdf/inbox 等。可说「桌面/xx」「笔记/xx」。",
        {"path": {"type": "string", "description": "路径：相对名、绝对路径，或 桌面/资料库/音乐/图片/工作区 别名。"}},
        ["path"],
    ),
    openai_tool(
        "write_file",
        "写入文本文件。未指明目录时按扩展名写入资料库子目录（txt→notes，无扩展名→inbox）。说「桌面」则写桌面。不要用 execute_code 写文件。",
        {
            "path": {"type": "string", "description": "如 备忘.txt、桌面/备忘.txt、合同.pdf。相对路径按类型自动进子目录。"},
            "content": {"type": "string", "description": "要写入的全文。"},
        },
        ["path"],
    ),
    openai_tool(
        "search_files",
        "按 glob 搜索文件。不指定 root 时：代码搜工作区；文档/媒体搜对应资料库子目录（*.txt→notes，*.pdf→pdf，*→inbox）。",
        {
            "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py、*.txt、*.pdf、*"},
            "root": {"type": "string", "description": "可选。笔记/收件箱/pdf目录/word目录/表格/幻灯片/音乐/图片/视频/桌面/工作区。"},
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
        "打开应用、网址，或打开资料库/桌面里的文件和文件夹。打开文件时用 Windows 默认程序（与资源管理器双击相同），不要指定播放器或 Word。浏览未分类文件夹用「收件箱」或「浏览」。",
        {"target": {"type": "string", "description": "应用名，或 笔记/收件箱/pdf目录/音乐/图片/视频/桌面/资料库/带扩展名的文件名。"}},
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
        "删除资料库、工作区或桌面上的一个文件（不能删目录、不能扫全盘）。必须 confirm=true，且用户本轮明确说了「确认」。",
        {
            "path": {"type": "string", "description": "相对名、别名或绝对路径。"},
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
    openai_tool(
        "find_capability",
        "现有工具交不了差时找路：搜正规数据源/API/文档，列出最多3条带来源链接的候选，并请示用户。用户要序列行情、缺接口、说「找路」时用。不要用新闻综述假装完成。不装包、不改程序。",
        {"need": {"type": "string", "description": "缺什么能力，例如：A股近半年收盘价和成交额。"}},
        ["need"],
    ),
    openai_tool(
        "list_capabilities",
        "列出已经和用户铺过的能力路（选定的源、是否还在等Key）。",
        {},
        [],
    ),
    openai_tool(
        "save_capability",
        "把用户选定的能力路记下来，下次同类问题先走这条。必须本轮说了「记下」或「确认」，且 confirm=true。不写密钥、不装包。",
        {
            "name": {"type": "string", "description": "能力短名，如 A股日线。"},
            "source": {"type": "string", "description": "选用的源或产品名。"},
            "status": {"type": "string", "description": "chosen / waiting_key / ready，或中文：已选定、等Key、可用。缺Key用 waiting_key。"},
            "homepage": {"type": "string", "description": "必须是本轮搜索里出现过的链接，没有就留空。"},
            "notes": {"type": "string", "description": "用户还要做什么，如去某页申请 token。"},
            "confirm": {"type": "boolean", "description": "仅当用户本轮说了记下或确认时为 true。"},
        },
        ["name", "source"],
    ),
    openai_tool(
        "set_capability_secret",
        "把用户提供的 API Key/Token 写入本机 .env，并让当前进程立刻能用。必须本轮说了确认。不要在对用户的话里复述密钥。不能覆盖 DEEPSEEK_API_KEY。",
        {
            "env_key": {"type": "string", "description": "如 QWEATHER_API_KEY、TUSHARE_TOKEN。"},
            "value": {"type": "string", "description": "密钥本身，只进 .env。"},
            "name": {"type": "string", "description": "对应的能力短名，如 天气、A股日线。"},
            "confirm": {"type": "boolean", "description": "用户本轮说了确认则为 true。"},
        },
        ["env_key", "value"],
    ),
    openai_tool(
        "install_capability_package",
        "仅安装白名单包到当前 venv：akshare、tushare。必须确认。不改小派源码。",
        {
            "package": {"type": "string", "description": "akshare 或 tushare。"},
            "confirm": {"type": "boolean"},
        },
        ["package"],
    ),
    openai_tool(
        "use_capability",
        "调用已经接上的能力。天气（和风 Key 或 Open-Meteo）；A 股日线（Tushare 或 akshare）。不支持的种类会明确拒绝。用户问天气、或给出股票代码查收盘成交额时用。",
        {
            "kind": {"type": "string", "description": "weather / stock / auto。"},
            "query": {"type": "string", "description": "用户原话，可含城市或股票代码。"},
        },
        [],
    ),
]


def schema_by_name() -> dict[str, dict[str, Any]]:
    """Index tool schemas by function name."""
    return {item["function"]["name"]: item for item in TOOL_SCHEMAS}
