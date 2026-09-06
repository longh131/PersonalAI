"""Wake phrase matching."""

from senses.wakeword import match_wake_phrase, wake_phrases_for


def test_wake_name_and_greeting() -> None:
    assert match_wake_phrase("织") == (True, "")
    assert match_wake_phrase("嗨织") == (True, "")
    assert match_wake_phrase("你好织") == (True, "")
    assert match_wake_phrase("你好，织") == (True, "")
    woke, rest = match_wake_phrase("嗨织，打开记事本")
    assert woke is True
    assert "打开记事本" in rest.replace("，", "")
    woke, rest = match_wake_phrase("你好织，打开记事本")
    assert woke is True
    assert "打开记事本" in rest.replace("，", "")
    woke, rest = match_wake_phrase("织，打开计算器")
    assert woke is True
    assert "打开计算器" in rest


def test_wake_homophone_after_greeting() -> None:
    assert match_wake_phrase("你好知") == (True, "")
    woke, rest = match_wake_phrase("嗨知打开记事本")
    assert woke is True
    assert "打开记事本" in rest
    assert match_wake_phrase("知") == (False, "")
    assert match_wake_phrase("你好知道") == (False, "")
    assert match_wake_phrase("小知识") == (False, "")


def test_wake_ignores_false_friends() -> None:
    assert match_wake_phrase("组织一次会议") == (False, "")
    assert match_wake_phrase("纺织厂在哪") == (False, "")
    assert match_wake_phrase("你好") == (False, "")
    assert match_wake_phrase("") == (False, "")


def test_wake_xiaopai() -> None:
    assert match_wake_phrase("你好小派", "小派") == (True, "")
    assert match_wake_phrase("嗨小拍，打开记事本", "小派")[0] is True
    assert match_wake_phrase("你好", "小派") == (False, "")
