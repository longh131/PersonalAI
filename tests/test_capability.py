"""Phase-1 meta-capability: find a path, ask, remember. No installs."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.capability import (
    candidates_from_search,
    format_find_result,
    looks_like_capability_gap,
    user_said_save,
)


def test_gap_markers() -> None:
    assert looks_like_capability_gap("帮我查茅台近半年收盘价和成交额")
    assert looks_like_capability_gap("这个做不到就帮我找路")
    assert not looks_like_capability_gap("今天天气怎么样")
    assert not looks_like_capability_gap("打开记事本")


def test_candidates_only_keep_search_urls() -> None:
    rows = [
        {"title": "Tushare 文档", "href": "https://tushare.pro/document", "body": "需要注册申请 token"},
        {"title": "编造", "href": "", "body": "http://evil.example/fake-api"},
        {"title": "新闻", "href": "https://news.example/stock", "body": "今日涨跌"},
    ]
    found = candidates_from_search(rows, max_results=3)
    hrefs = [item["href"] for item in found]
    assert hrefs == ["https://tushare.pro/document", "https://news.example/stock"]
    assert found[0]["needs_key"] == "是"
    assert "evil.example" not in format_find_result("日线", candidates=found)


def test_save_tokens() -> None:
    assert user_said_save("先记下用 Tushare，Key 我去申请")
    assert user_said_save("确认记下")
    assert not user_said_save("先看看有哪些源")


@pytest.mark.asyncio
async def test_save_requires_spoken_ok(tmp_path: Path) -> None:
    from config.settings import Settings
    from memory.manager import MemoryManager, hash_embed
    from tools.builtin import BuiltinTools

    settings = Settings(sqlite_path=str(tmp_path / "c.db"), deepseek_api_key="x")
    memory = MemoryManager(settings, encode=hash_embed)
    await memory.initialize()
    tools = BuiltinTools(settings, memory=memory)

    tools.last_user_text = "有哪些源"
    denied = await tools.save_capability("A股日线", "Tushare", confirm=True)
    assert "记下" in denied
    assert await memory.capabilities.list_active() == []

    tools.last_user_text = "先记下用 Tushare"
    ok = await tools.save_capability(
        "A股日线",
        "Tushare",
        status="waiting_key",
        homepage="https://tushare.pro/document",
        notes="用户去申请 token",
        confirm=True,
    )
    assert "已记下" in ok
    rows = await memory.capabilities.list_active()
    assert len(rows) == 1
    assert rows[0]["source"] == "Tushare"
    assert rows[0]["status"] == "waiting_key"

    bundle = await memory.weave("收盘价", "s1")
    assert "A股日线" in bundle.capability_block
    listed = await tools.list_capabilities()
    assert "Tushare" in listed
    await memory.close()


@pytest.mark.asyncio
async def test_parse_input_marks_capability() -> None:
    from core.nodes import GraphNodes

    nodes = GraphNodes(
        memory=None,  # type: ignore[arg-type]
        llm=None,  # type: ignore[arg-type]
        tools=None,  # type: ignore[arg-type]
        identity=None,  # type: ignore[arg-type]
        max_tool_iterations=3,
    )
    state = await nodes.parse_input({"user_input": "查一下近半年收盘价和成交额", "session_id": "s"})
    assert state["parsed_intent"]["kind"] == "capability"
    weather = await nodes.parse_input({"user_input": "今天天气怎么样", "session_id": "s"})
    assert weather["parsed_intent"]["kind"] == "weather"


@pytest.mark.asyncio
async def test_secret_and_install_gates(tmp_path: Path) -> None:
    from config.settings import Settings
    from memory.manager import MemoryManager, hash_embed
    from tools.builtin import BuiltinTools
    from tools.envfile import upsert_env_value
    from tools.runners import extract_stock_code, refuse

    env_file = tmp_path / ".env"
    upsert_env_value(env_file, "TUSHARE_TOKEN", "abc123")
    text = env_file.read_text(encoding="utf-8")
    assert "TUSHARE_TOKEN=abc123" in text

    settings = Settings(sqlite_path=str(tmp_path / "c.db"), deepseek_api_key="x")
    memory = MemoryManager(settings, encode=hash_embed)
    await memory.initialize()
    tools = BuiltinTools(settings, memory=memory)
    tools.env_path = env_file

    tools.last_user_text = "这是我的token"
    denied = await tools.set_capability_secret("TUSHARE_TOKEN", "secret-value", confirm=True)
    assert "确认" in denied
    tools.last_user_text = "确认写入密钥"
    ok = await tools.set_capability_secret(
        "TUSHARE_TOKEN", "secret-value", name="A股日线", confirm=True
    )
    assert "TUSHARE_TOKEN" in ok
    assert "secret-value" not in ok
    assert "secret-value" not in (await tools.list_capabilities())

    blocked = await tools.set_capability_secret("DEEPSEEK_API_KEY", "x", confirm=True)
    assert "不能通过对话覆盖" in blocked or "确认" in blocked
    tools.last_user_text = "确认写入"
    blocked2 = await tools.set_capability_secret("DEEPSEEK_API_KEY", "x", confirm=True)
    assert "不能通过对话覆盖" in blocked2

    tools.last_user_text = "确认安装 numpy"
    pip = await tools.install_capability_package("numpy", confirm=True)
    assert "拒绝" in pip
    assert extract_stock_code("茅台 600519 近半年") == "600519.SH"
    assert "做不到" in refuse("无适配器", "X", "换需求")
    await memory.close()

