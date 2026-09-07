"""Parse search hits into capability-path candidates. No installs, no invented URLs."""

from __future__ import annotations

from typing import Any

GAP_MARKERS = (
    "找路",
    "铺路",
    "没有数据源",
    "没有行情源",
    "缺接口",
    "缺能力",
    "做不到就帮我找",
    "收盘价",
    "成交额",
    "历史行情",
    "日线",
    "k线",
    "K线",
    "具体行情",
)

KEY_HINTS = ("token", "apikey", "api key", "api_key", "申请", "注册", "密钥", "key")
SAVE_TOKENS = ("确认", "确定", "confirm", "记下", "就用", "用这条", "用这个", "先记下", "选这个")
PIP_WHITELIST = frozenset({"akshare", "tushare"})


def looks_like_capability_gap(text: str) -> bool:
    """True when the user needs a missing data source or asked to find a path."""
    raw = text or ""
    return any(token in raw for token in GAP_MARKERS)


def user_said_save(text: str) -> bool:
    raw = text or ""
    lowered = raw.lower()
    return any(token in raw or token in lowered for token in SAVE_TOKENS)


def candidates_from_search(rows: list[dict[str, Any]], *, max_results: int = 3) -> list[dict[str, str]]:
    """Keep only http(s) links that actually appeared in search rows."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        href = str(row.get("href") or row.get("url") or "").strip()
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        title = str(row.get("title") or href).strip()
        body = str(row.get("body") or row.get("snippet") or "").strip()
        blob = f"{title} {body} {href}".lower()
        needs_key = any(hint in blob for hint in KEY_HINTS)
        out.append(
            {
                "title": title,
                "href": href,
                "body": body[:160],
                "needs_key": "是" if needs_key else "不明确",
            }
        )
        if len(out) >= max_results:
            break
    return out


def format_saved_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    from memory.capabilities import STATUS_LABEL

    lines = ["已铺过、可能对上这次缺口的路："]
    for row in rows:
        label = STATUS_LABEL.get(str(row.get("status") or ""), row.get("status"))
        lines.append(f"- {row.get('name')} → {row.get('source') or '未指定'}（{label}）")
    return "\n".join(lines)


def format_find_result(
    need: str,
    *,
    saved: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, str]] | None = None,
    search_error: str = "",
) -> str:
    parts = [f"缺口：{(need or '').strip() or '未说明'}。新闻综述交不了序列数据的差，需要专用源。"]
    saved_text = format_saved_rows(saved or [])
    if saved_text:
        parts.append(saved_text)
    if search_error:
        parts.append(f"查找失败：{search_error}")
    elif candidates:
        parts.append("候选（链接均来自本轮搜索，未核实能否调用）：")
        for index, item in enumerate(candidates, start=1):
            parts.append(
                f"{index}. {item['title']}\n"
                f"   链接：{item['href']}\n"
                f"   摘要：{item['body'] or '（无）'}\n"
                f"   可能需要Key：{item['needs_key']}"
            )
    else:
        parts.append("没有从搜索里拿到可核对的链接。不能编造接口。")
    parts.append("请选一条，并说「记下」或「确认记下」后才写入。未确认不装包、不改程序、不写密钥。")
    return "\n".join(parts)
