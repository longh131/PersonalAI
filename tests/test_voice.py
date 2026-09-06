"""Voice helpers: SenseVoice tag stripping and resampling."""

from pathlib import Path

import numpy as np

from senses.voice import (
    SpeakOutcome,
    find_sensevoice_files,
    looks_like_stt_hallucination,
    strip_sensevoice_tags,
)
from senses.voice import _for_speech, _resample, _rms


def test_strip_sensevoice_tags() -> None:
    raw = "<|zh|><|NEUTRAL|><|Speech|>今天天气不错"
    assert strip_sensevoice_tags(raw) == "今天天气不错"
    assert strip_sensevoice_tags("你好") == "你好"
    assert strip_sensevoice_tags("") == ""


def test_stt_hallucination_filter() -> None:
    assert looks_like_stt_hallucination("以下是普通话的句子。") is True
    assert looks_like_stt_hallucination("证证证证证证证") is True
    assert looks_like_stt_hallucination("你好织") is False
    assert looks_like_stt_hallucination("") is False


def test_resample_length() -> None:
    src = np.ones(16000, dtype=np.float32)
    dst = _resample(src, 16000, 8000)
    assert 7900 < dst.size < 8100
    same = _resample(src, 16000, 16000)
    assert same.size == src.size


def test_rms_and_speech_strip() -> None:
    silence = np.zeros(100, dtype=np.float32)
    assert _rms(silence) < 1e-4
    spoken = _for_speech("结论\n```python\nprint(1)\n```\n补充")
    assert "print" not in spoken
    assert "结论" in spoken


def test_find_sensevoice_missing(tmp_path: Path) -> None:
    assert find_sensevoice_files(tmp_path) is None


def test_speak_outcome_defaults() -> None:
    outcome = SpeakOutcome()
    assert outcome.interrupted is False
    assert outcome.reason == "finished"
    assert outcome.audio is None
