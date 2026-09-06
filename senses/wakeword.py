"""Wake-word matching and a background VAD watcher for standby."""

from __future__ import annotations

import queue
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from loguru import logger

from senses.voice import SAMPLE_RATE, CHUNK_SECONDS, VoiceIO

_FALSE_PREFIXES = ("组织", "织物", "纺织", "交织", "织布", "织金", "织造")

# STT often writes 织 as 知/之. Only accepted after a greeting, never as a bare wake.
_NAME_HOMOPHONES = {
    "织": ("织", "知", "之", "支", "芝"),
    "小派": ("小派", "小拍"),
    "派": ("派", "拍"),
}
_ZHI_CONTINUATIONS = ("识", "道", "晓", "足", "情", "名", "己")
_GREETINGS = ("你好啊", "你好呀", "你好", "嗨", "嘿", "喂", "小")


def wake_phrases_for(name: str) -> list[str]:
    """Build spoken wake phrases from the assistant's display name."""
    alias = (name or "织").strip() or "织"
    phrases = [
        f"你好{alias}",
        f"嗨{alias}",
        f"嘿{alias}",
        f"嘿，{alias}",
        f"嗨，{alias}",
        f"小{alias}",
        f"hey {alias}",
        f"hi {alias}",
        alias,
    ]
    return list(dict.fromkeys(phrases))


def _name_sounds(name: str) -> tuple[str, ...]:
    alias = (name or "织").strip() or "织"
    extras = _NAME_HOMOPHONES.get(alias, ())
    return tuple(dict.fromkeys((alias, *extras)))


def match_wake_phrase(text: str, name: str = "织") -> tuple[bool, str]:
    """Return (matched, remainder) if `text` starts with a wake phrase.

    「你好织 / 嗨织」会唤醒；STT 把织听成「知」时，在问候语后也算唤醒。
    「你好」单独、以及「组织 / 纺织 / 你好知道」不会唤醒。
    「织，打开记事本」会唤醒并把余下命令交出。
    """
    raw = (text or "").strip()
    if not raw:
        return False, ""
    compact = re.sub(r"[\s,，。.!！、?？:：]", "", raw)
    if any(compact.startswith(prefix) for prefix in _FALSE_PREFIXES):
        return False, ""
    alias = (name or "织").strip() or "织"
    compact_l = compact.lower()
    for greet in _GREETINGS:
        for sound in _name_sounds(alias):
            token = greet + sound
            token_l = token.lower()
            if not compact_l.startswith(token_l):
                continue
            rest = compact[len(token) :]
            if sound == "知" and rest.startswith(_ZHI_CONTINUATIONS):
                continue
            return True, rest.strip()
    phrases = wake_phrases_for(alias)
    for phrase in sorted(phrases, key=len, reverse=True):
        token = re.sub(r"[\s,，。.!！、?？:：]", "", phrase).lower()
        if not token:
            continue
        if compact_l == token:
            return True, ""
        if compact_l.startswith(token) and len(token) >= 2:
            rest = compact[len(token) :].strip()
            return True, rest
    spaced = re.match(rf"^{re.escape(alias)}[,，。.!\s]+(.+)$", raw)
    if spaced:
        return True, spaced.group(1).strip()
    return False, ""


class WakeWatcher:
    """Idle-only mic watcher: VAD → transcribe → wake phrase → callback."""

    def __init__(
        self,
        voice: VoiceIO,
        *,
        name: str,
        on_wake: Callable[[str], None],
        stop: threading.Event,
        settings: Any | None = None,
    ) -> None:
        self.voice = voice
        self.name = name
        self.on_wake = on_wake
        self.stop = stop
        self.paused = threading.Event()
        self._abort = threading.Event()
        self.start_energy = float(getattr(settings, "voice_start_energy", 0.0025))
        self.keep_energy = float(getattr(settings, "voice_keep_energy", 0.0012))
        self.start_ratio = float(getattr(settings, "voice_start_ratio", 8.0))
        self.keep_ratio = float(getattr(settings, "voice_keep_ratio", 4.0))
        self.silence_end = float(getattr(settings, "voice_silence_end", 1.0))
        self.max_utt = min(float(getattr(settings, "wake_max_utterance", 4.0)), 6.0)

    def pause(self) -> None:
        """Release the microphone so a voice session can use it."""
        self.paused.set()
        self._abort.set()

    def resume(self) -> None:
        """Start watching again after a voice session ends."""
        self._abort.clear()
        self.paused.clear()

    def start(self) -> None:
        """Spawn the background watcher thread."""
        threading.Thread(target=self._loop, daemon=True, name="wake-word").start()

    def _loop(self) -> None:
        if not self.voice.available:
            logger.info("Wake word skipped: microphone unavailable")
            return
        logger.info("Wake word watching as 「{}」", self.name)
        while not self.stop.is_set():
            if self.paused.is_set():
                time.sleep(0.2)
                continue
            self._abort.clear()
            audio = self._record_idle_utterance()
            if self.stop.is_set() or self.paused.is_set():
                continue
            if audio is None:
                continue
            clip = np.asarray(audio, dtype=np.float32).reshape(-1)
            peak = float(np.max(np.abs(clip))) if clip.size else 0.0
            if peak < 0.04:
                logger.debug("wake skip quiet peak={:.5f}", peak)
                continue
            try:
                text = self.voice.transcribe_audio(clip)
            except Exception as exc:  # noqa: BLE001
                logger.warning("wake transcribe failed: {}", exc)
                continue
            if self.stop.is_set() or self.paused.is_set():
                continue
            if not text:
                continue
            woke, rest = match_wake_phrase(text, self.name)
            if not woke:
                logger.debug("wake ignore {}", text)
                continue
            logger.info("Wake phrase heard: {} remainder={}", text, rest)
            try:
                self.on_wake(rest)
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_wake failed: {}", exc)

    def _record_idle_utterance(self) -> np.ndarray | None:
        """Capture one short utterance or return None on pause/timeout slice."""
        try:
            import sounddevice as sd
        except Exception:
            time.sleep(1)
            return None
        blocks: queue.Queue[np.ndarray] = queue.Queue()

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if status:
                logger.debug("wake mic {}", status)
            blocks.put(np.copy(indata))

        stream_kwargs: dict[str, Any] = {
            "samplerate": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
            "blocksize": int(SAMPLE_RATE * CHUNK_SECONDS),
            "callback": callback,
        }
        device = getattr(self.voice, "_input_device", None)
        if device is not None:
            stream_kwargs["device"] = device
        voiced: list[np.ndarray] = []
        started = False
        silent = 0.0
        spoken = 0.0
        waited = 0.0
        noise_vals: list[float] = []
        noise = 0.0003
        slice_s = 8.0
        try:
            with sd.InputStream(**stream_kwargs):
                while not self.stop.is_set() and not self.paused.is_set() and not self._abort.is_set():
                    try:
                        block = blocks.get(timeout=0.25)
                    except queue.Empty:
                        waited += 0.25
                        if not started and waited >= slice_s:
                            return None
                        continue
                    samples = np.squeeze(block).astype(np.float32)
                    energy = float(np.sqrt(np.mean(np.square(samples)) + 1e-9))
                    dt = float(samples.size) / SAMPLE_RATE
                    if not started:
                        waited += dt
                        noise_vals.append(energy)
                        noise = float(np.median(noise_vals[-25:]))
                        start_at = max(self.start_energy, noise * self.start_ratio)
                        if energy >= start_at:
                            started = True
                            voiced.append(samples)
                            spoken = dt
                            silent = 0.0
                        elif waited >= slice_s:
                            return None
                        continue
                    voiced.append(samples)
                    spoken += dt
                    keep_at = max(self.keep_energy, noise * self.keep_ratio)
                    if energy < keep_at:
                        silent += dt
                    else:
                        silent = 0.0
                    if spoken >= 0.35 and silent >= self.silence_end:
                        break
                    if spoken >= self.max_utt:
                        break
        except Exception as exc:  # noqa: BLE001
            logger.debug("wake stream: {}", exc)
            time.sleep(0.4)
            return None
        if not voiced:
            return None
        return np.concatenate(voiced)
