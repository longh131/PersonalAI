"""Standby helpers: end-session keywords and hotkey parsing."""

from senses.standby import _is_end_session, _parse_hotkey


def test_end_session_exact_and_spoken() -> None:
    assert _is_end_session("退出")
    assert _is_end_session("退出。")
    assert _is_end_session("退出吧")
    assert _is_end_session("我要退出了")
    assert _is_end_session("好了退出")
    assert _is_end_session("待机")
    assert _is_end_session("不用了")
    assert _is_end_session("没事了")
    assert _is_end_session("停止聆听")


def test_end_session_rejects_normal_talk() -> None:
    assert not _is_end_session("你好")
    assert not _is_end_session("")
    assert not _is_end_session("今天天气怎么样")
    assert not _is_end_session("退出登录怎么搞")
    assert not _is_end_session("不要退出")


def test_parse_ctrl_shift_l() -> None:
    parsed = _parse_hotkey("CTRL+SHIFT+L")
    assert parsed is not None
    mods, main = parsed
    assert main == 0x4C
    assert 0x11 in mods
    assert 0x10 in mods
