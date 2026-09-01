"""On-device SenseVoice STT, edge-tts playback, and barge-in listening."""

from __future__ import annotations

import asyncio
import io
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from config.settings import PROJECT_ROOT

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.12
SILENCE_SECONDS = 1.15
MAX_UTTERANCE = 18.0
LISTEN_SECONDS = 5.0
START_ENERGY = 0.0025
KEEP_ENERGY = 0.0012
_SENSEVOICE_TAG = re.compile(r"<\|[^|>]*\|>")
EDGE_VOICE_LABELS = {
    "zh-CN-XiaoxiaoNeural": "晓晓",
    "zh-CN-YunxiNeural": "云希",
    "zh-CN-YunyangNeural": "云扬",
    "zh-CN-XiaoyiNeural": "晓伊",
    "zh-CN-YunjianNeural": "云健",
}


@dataclass
class SpeakOutcome:
    """Result of a TTS turn that may have been interrupted."""

    interrupted: bool = False
    reason: str = "finished"
    audio: np.ndarray | None = None


class VoiceIO:
    """Microphone listener and speaker. Degrades to disabled if hardware/models fail."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        tts_enabled: bool = True,
        model_size: str = "base",
        settings: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self.tts_enabled = tts_enabled
        self.model_size = model_size
        self.settings = settings
        self.available = False
        self.tts_available = False
        self._whisper: Any = None
        self._sensevoice: Any = None
        self._tts: Any = None
        self._tts_lock = threading.Lock()
        self._listen_lock = asyncio.Lock()
        self._input_device: int | None = None
        self.paused = False
        self._abort = threading.Event()
        self._tts_stop = threading.Event()
        self.silence_end = float(getattr(settings, "voice_silence_end", 1.0))
        self.min_utterance = float(getattr(settings, "voice_min_utterance", 0.4))
        self.max_utterance = float(getattr(settings, "voice_max_utterance", 15.0))
        self.followup_seconds = float(getattr(settings, "voice_followup_seconds", 10.0))
        self.start_timeout = float(getattr(settings, "voice_start_timeout", 8.0))
        self.start_energy = float(getattr(settings, "voice_start_energy", START_ENERGY))
        self.keep_energy = float(getattr(settings, "voice_keep_energy", KEEP_ENERGY))
        self.start_ratio = float(getattr(settings, "voice_start_ratio", 8.0))
        self.keep_ratio = float(getattr(settings, "voice_keep_ratio", 4.0))
        self.hotkey = str(getattr(settings, "voice_hotkey", "CTRL+SHIFT+L") or "CTRL+SHIFT+L")
        self.barge_in_grace = float(getattr(settings, "voice_barge_in_grace", 0.4))
        self.barge_in_energy = float(getattr(settings, "voice_barge_in_energy", 0.02))
        self.barge_in_ratio = float(getattr(settings, "voice_barge_in_ratio", 2.5))
        self.stt_engine = str(getattr(settings, "stt_engine", "sensevoice") or "sensevoice").lower()
        self.tts_engine = str(getattr(settings, "tts_engine", "edge") or "edge").lower()
        self.tts_voice = str(getattr(settings, "tts_voice", "zh-CN-XiaoxiaoNeural") or "zh-CN-XiaoxiaoNeural")
        self.sensevoice_dir = Path(getattr(settings, "sensevoice_dir", "models/sensevoice") or "models/sensevoice")
        if not self.sensevoice_dir.is_absolute():
            self.sensevoice_dir = PROJECT_ROOT / self.sensevoice_dir
        self.tts_voices: list[str] = []
        self.tts_voice_name = ""
        self.stt_label = "未加载"

    async def initialize(self) -> None:
        """Load STT and probe TTS in a worker thread."""
        if not self.enabled:
            logger.info("Voice disabled by settings")
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        """Blocking init for mic, SenseVoice / Whisper, and TTS backends."""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            self._input_device = _select_input_device(devices, sd.default.device[0])
            self.available = True
            logger.info("Microphone backend ready, {} devices, input={}", len(devices), self._input_device)
        except Exception as exc:  # noqa: BLE001
            self.available = False
            logger.warning("sounddevice unavailable: {}", exc)
        self._sensevoice = None
        self._whisper = None
        if self.stt_engine in {"sensevoice", "auto"}:
            self._sensevoice = _load_sensevoice(self.sensevoice_dir)
            if self._sensevoice is not None:
                self.stt_label = "SenseVoice（本地）"
        if self._sensevoice is None:
            self._whisper = _load_whisper(self.model_size)
            if self._whisper is not None:
                self.stt_label = f"Whisper {self.model_size}（本地备用）"
            else:
                self.stt_label = "在线备用识别"
                logger.warning("SenseVoice and Whisper unavailable; STT will use online fallback")
        if self.tts_enabled:
            self._probe_tts()

    def _probe_tts(self) -> None:
        """Mark TTS available without holding a live SAPI engine."""
        edge_ok = False
        if self.tts_engine != "sapi":
            try:
                import edge_tts  # noqa: F401

                edge_ok = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("edge-tts unavailable: {}", exc)
        sapi_ok = False
        try:
            import pyttsx3  # noqa: F401

            sapi_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("pyttsx3 unavailable: {}", exc)
        self.tts_available = edge_ok or sapi_ok
        self._tts = True if self.tts_available else None
        if edge_ok:
            label = EDGE_VOICE_LABELS.get(self.tts_voice, self.tts_voice)
            self.tts_voice_name = f"edge-tts {label}"
            self.tts_voices = [f"edge-tts {name} ({vid})" for vid, name in EDGE_VOICE_LABELS.items()]
            logger.info("TTS edge-tts ready voice={}", self.tts_voice)
            return
        if sapi_ok:
            self.tts_voice_name = "系统语音（SAPI）"
            self.tts_voices = ["Microsoft Huihui / Zira"]
            logger.info("TTS SAPI fallback ready")

    def abort(self) -> None:
        """Ask the current recording loop to stop (keyboard interrupt, new /listen)."""
        self._abort.set()

    def interrupt_speech(self) -> None:
        """Stop current playback so barge-in or a hotkey can take over."""
        self._tts_stop.set()

    async def listen_turn(self, *, wait_for_start: float) -> str:
        """Capture one utterance: wait for speech, stop after a pause, then transcribe."""
        if not self.available:
            return ""
        self._abort.clear()
        async with self._listen_lock:
            await self._wait_tts_idle()
            logger.info("listen_turn start wait={:.1f}s", wait_for_start)
            audio = await asyncio.to_thread(
                self._record_turn,
                wait_for_start,
                self.silence_end,
                self.min_utterance,
                self.max_utterance,
            )
        return await asyncio.to_thread(self._prepare_and_transcribe, audio)

    async def listen_utterance(self, *, timeout: float | None = None) -> str:
        """Compatibility wrapper: one turn with the given start-wait timeout."""
        return await self.listen_turn(wait_for_start=timeout or self.start_timeout)

    async def listen_seconds(self, seconds: float = LISTEN_SECONDS) -> str:
        """Record a fixed duration. Kept as a fallback path."""
        if not self.available:
            return ""
        self._abort.clear()
        async with self._listen_lock:
            audio = await asyncio.to_thread(self._record_fixed, seconds)
        return await asyncio.to_thread(self._prepare_and_transcribe, audio)

    def transcribe_audio(self, audio: np.ndarray | None) -> str:
        """Transcribe a captured clip (used after barge-in)."""
        return self._prepare_and_transcribe(audio)

    def _record_kwargs(self) -> dict[str, Any]:
        """Common sounddevice arguments, pinning the chosen input device when known."""
        kwargs: dict[str, Any] = {"samplerate": SAMPLE_RATE, "channels": 1, "dtype": "float32"}
        if self._input_device is not None:
            kwargs["device"] = self._input_device
        return kwargs

    def _record_fixed(self, seconds: float) -> np.ndarray | None:
        """Record `seconds` of mono audio starting immediately (no VAD)."""
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            logger.warning("sounddevice missing: {}", exc)
            return None
        frames = max(int(SAMPLE_RATE * seconds), SAMPLE_RATE)
        try:
            audio = sd.rec(frames, **self._record_kwargs())
            sd.wait()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Microphone error: {}", exc)
            return None
        return np.squeeze(np.asarray(audio, dtype=np.float32))

    def _record_turn(
        self,
        wait_for_start: float,
        silence_end: float,
        min_utt: float,
        max_utt: float,
    ) -> np.ndarray | None:
        """Record via a continuous InputStream until pause-endpointing fires."""
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            logger.warning("sounddevice missing: {}", exc)
            return None
        blocks: queue.Queue[np.ndarray] = queue.Queue()

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if status:
                logger.debug("mic status {}", status)
            blocks.put(np.copy(indata))

        stream_kwargs: dict[str, Any] = {
            "samplerate": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
            "blocksize": int(SAMPLE_RATE * CHUNK_SECONDS),
            "callback": callback,
        }
        if self._input_device is not None:
            stream_kwargs["device"] = self._input_device
        voiced: list[np.ndarray] = []
        started = False
        silent = 0.0
        waited = 0.0
        spoken = 0.0
        noise_vals: list[float] = []
        noise = 0.0003
        logger.info("Opening microphone stream device={}", self._input_device)
        try:
            with sd.InputStream(**stream_kwargs):
                logger.info("Microphone stream open")
                while True:
                    if self._abort.is_set():
                        logger.debug("listen aborted")
                        return None
                    try:
                        block = blocks.get(timeout=0.25)
                    except queue.Empty:
                        waited += 0.25
                        if not started and waited >= wait_for_start:
                            logger.info("listen timeout waiting for speech ({:.1f}s)", wait_for_start)
                            return None
                        continue
                    samples = np.squeeze(block).astype(np.float32)
                    energy = _rms(samples)
                    dt = float(samples.size) / SAMPLE_RATE
                    if not started:
                        waited += dt
                        noise_vals.append(energy)
                        noise = float(np.median(noise_vals[-25:]))
                        start_at = max(self.start_energy, noise * self.start_ratio)
                        if energy >= start_at:
                            started = True
                            voiced.append(samples)
                            silent = 0.0
                            spoken = dt
                            logger.info("speech start energy={:.5f} thresh={:.5f}", energy, start_at)
                        elif waited >= wait_for_start:
                            logger.info("VAD timeout, noise={:.5f} last={:.5f}", noise, energy)
                            return None
                        continue
                    voiced.append(samples)
                    spoken += dt
                    keep_at = max(self.keep_energy, noise * self.keep_ratio)
                    if energy < keep_at:
                        silent += dt
                    else:
                        silent = 0.0
                    if spoken >= min_utt and silent >= silence_end:
                        logger.info("speech end uttered={:.2f}s pause={:.2f}s", spoken, silent)
                        break
                    if spoken >= max_utt:
                        logger.info("speech hit max utterance {:.1f}s", max_utt)
                        break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Microphone stream error: {}", exc)
            return None
        if not voiced:
            return None
        return np.concatenate(voiced)

    def _prepare_and_transcribe(self, audio: np.ndarray | None) -> str:
        """Normalize a clip and run STT. Returns empty string for silence."""
        if audio is None:
            return ""
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size < SAMPLE_RATE // 5:
            return ""
        peak = float(np.max(np.abs(samples)))
        rms = float(np.sqrt(np.mean(np.square(samples)) + 1e-12))
        logger.info("Captured audio peak={:.5f} rms={:.5f} sec={:.2f}", peak, rms, samples.size / SAMPLE_RATE)
        if peak < 5e-4:
            logger.info("Clip too quiet, skip STT")
            return ""
        if peak < 0.45:
            samples = np.clip(samples * min(0.45 / peak, 50.0), -1.0, 1.0)
        return self._transcribe(samples)

    def _transcribe(self, audio: np.ndarray) -> str:
        """Transcribe with SenseVoice, then Whisper, then online fallback."""
        if self._sensevoice is not None:
            try:
                text = _transcribe_sensevoice(self._sensevoice, audio)
                if text:
                    logger.info("STT sensevoice {}", text)
                    return text
            except Exception as exc:  # noqa: BLE001
                logger.warning("SenseVoice STT failed: {}", exc)
        if self._whisper is not None:
            try:
                segments, info = self._whisper.transcribe(
                    audio,
                    language="zh",
                    beam_size=5,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    initial_prompt="以下是普通话的句子。",
                )
                text = "".join(segment.text for segment in segments).strip()
                if text:
                    logger.info("STT whisper ({}) {}", getattr(info, "language", "?"), text)
                    return text
            except Exception as exc:  # noqa: BLE001
                logger.warning("Whisper STT failed: {}", exc)
        text = _transcribe_google(audio)
        if text:
            logger.info("STT google {}", text)
        return text

    async def _wait_tts_idle(self) -> None:
        """Yield until the speaker is not holding the audio device."""
        while self._tts_lock.locked():
            await asyncio.sleep(0.05)

    async def speak(self, text: str) -> SpeakOutcome:
        """Speak text; equivalent to speak_with_barge_in without using the result."""
        return await self.speak_with_barge_in(text)

    async def speak_with_barge_in(self, text: str) -> SpeakOutcome:
        """Play TTS while listening; stop and capture audio if the user talks over it."""
        cleaned = _for_speech(text)
        if not cleaned or not self.tts_available:
            return SpeakOutcome()
        self._tts_stop.clear()
        samples: np.ndarray | None = None
        play_sr = SAMPLE_RATE
        if self.tts_engine != "sapi":
            synthesized = await self._synth_edge(cleaned)
            if synthesized is not None:
                samples, play_sr = synthesized
        if self._tts_stop.is_set():
            return SpeakOutcome(interrupted=True, reason="hotkey")
        if samples is None:
            logger.info("TTS falling back to SAPI")
            return await asyncio.to_thread(self._sapi_with_barge_in, cleaned)
        playback = _resample(samples, play_sr, SAMPLE_RATE)
        return await asyncio.to_thread(self._play_with_barge_in, playback)

    async def _synth_edge(self, text: str) -> tuple[np.ndarray, int] | None:
        """Synthesize MP3 via Microsoft Edge neural voices, then decode to PCM."""
        try:
            import edge_tts
        except Exception as exc:  # noqa: BLE001
            logger.warning("edge-tts import failed: {}", exc)
            return None
        buf = bytearray()
        try:
            communicate = edge_tts.Communicate(text, self.tts_voice)
            async for chunk in communicate.stream():
                if self._tts_stop.is_set():
                    return None
                if chunk.get("type") == "audio":
                    buf.extend(chunk.get("data") or b"")
        except Exception as exc:  # noqa: BLE001
            logger.warning("edge-tts synthesis failed: {}", exc)
            return None
        if not buf:
            return None
        decoded = _mp3_to_float32(bytes(buf))
        if decoded is None:
            logger.warning("edge-tts audio decode failed")
            return None
        return decoded

    def _play_with_barge_in(self, playback: np.ndarray) -> SpeakOutcome:
        """Play float32 mono 16 kHz audio and watch the mic for barge-in."""
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            logger.warning("sounddevice missing: {}", exc)
            return SpeakOutcome()
        playback = np.asarray(playback, dtype=np.float32).reshape(-1)
        if playback.size < SAMPLE_RATE // 20:
            return SpeakOutcome()
        mic_q: queue.Queue[np.ndarray] = queue.Queue()
        play_state = {"idx": 0, "done": False}

        def out_cb(outdata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if status:
                logger.debug("speaker status {}", status)
            if self._tts_stop.is_set() or play_state["idx"] >= playback.size:
                outdata[:] = 0
                play_state["done"] = True
                raise sd.CallbackStop
            idx = play_state["idx"]
            take = min(frames, playback.size - idx)
            outdata[:take, 0] = playback[idx : idx + take]
            if take < frames:
                outdata[take:] = 0
                play_state["done"] = True
            play_state["idx"] = idx + take
            if play_state["done"]:
                raise sd.CallbackStop

        def in_cb(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if status:
                logger.debug("barge-in mic status {}", status)
            mic_q.put(np.copy(np.squeeze(indata)))

        in_kwargs: dict[str, Any] = {
            "samplerate": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
            "blocksize": int(SAMPLE_RATE * CHUNK_SECONDS),
            "callback": in_cb,
        }
        if self._input_device is not None:
            in_kwargs["device"] = self._input_device
        out_kwargs: dict[str, Any] = {
            "samplerate": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
            "blocksize": int(SAMPLE_RATE * CHUNK_SECONDS),
            "callback": out_cb,
        }
        with self._tts_lock:
            try:
                with sd.InputStream(**in_kwargs), sd.OutputStream(**out_kwargs):
                    return self._watch_barge_in(mic_q, lambda: not play_state.get("done"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("barge-in duplex failed, play only: {}", exc)
                try:
                    sd.play(playback, SAMPLE_RATE)
                    sd.wait()
                except Exception as play_exc:  # noqa: BLE001
                    logger.warning("playback failed: {}", play_exc)
                return SpeakOutcome()

    def _watch_barge_in(self, mic_q: queue.Queue[np.ndarray], still_playing: Any) -> SpeakOutcome:
        """Consume mic blocks until playback ends or the user talks over it."""
        import time as time_mod

        grace_until = time_mod.monotonic() + max(self.barge_in_grace, 0.0)
        noise_vals: list[float] = []
        voiced: list[np.ndarray] = []
        started = False
        silent = 0.0
        spoken = 0.0
        noise = 0.001
        while True:
            playing = bool(still_playing())
            if self._abort.is_set():
                self._tts_stop.set()
                return SpeakOutcome(interrupted=True, reason="hotkey")
            try:
                block = mic_q.get(timeout=0.12)
            except queue.Empty:
                if not started and (not playing or self._tts_stop.is_set()):
                    if self._tts_stop.is_set() and playing:
                        return SpeakOutcome(interrupted=True, reason="hotkey")
                    return SpeakOutcome()
                continue
            samples = np.asarray(block, dtype=np.float32).reshape(-1)
            energy = _rms(samples)
            dt = float(samples.size) / SAMPLE_RATE
            now = time_mod.monotonic()
            if not started:
                if now < grace_until:
                    noise_vals.append(energy)
                    continue
                if noise_vals:
                    noise = float(np.median(noise_vals[-40:]))
                thresh = max(self.barge_in_energy, noise * self.barge_in_ratio)
                if energy >= thresh:
                    started = True
                    self._tts_stop.set()
                    voiced.append(samples)
                    spoken = dt
                    silent = 0.0
                    logger.info("barge-in start energy={:.5f} thresh={:.5f}", energy, thresh)
                    continue
                if not playing:
                    return SpeakOutcome()
                if self._tts_stop.is_set():
                    return SpeakOutcome(interrupted=True, reason="hotkey")
                continue
            voiced.append(samples)
            spoken += dt
            keep_at = max(self.keep_energy, noise * self.keep_ratio)
            if energy < keep_at:
                silent += dt
            else:
                silent = 0.0
            if spoken >= self.min_utterance and silent >= self.silence_end:
                logger.info("barge-in end uttered={:.2f}s pause={:.2f}s", spoken, silent)
                break
            if spoken >= self.max_utterance:
                logger.info("barge-in hit max utterance")
                break
        audio = np.concatenate(voiced) if voiced else None
        return SpeakOutcome(interrupted=True, reason="barge_in", audio=audio)

    def _sapi_with_barge_in(self, text: str) -> SpeakOutcome:
        """SAPI fallback: speak sentence chunks; mic barge-in stops later chunks."""
        self._tts_stop.clear()
        worker = threading.Thread(target=self._sapi_say_chunks, args=(text,), daemon=True, name="sapi-tts")
        worker.start()
        mic_q: queue.Queue[np.ndarray] = queue.Queue()
        try:
            import sounddevice as sd
        except Exception:
            worker.join(timeout=45)
            return SpeakOutcome()

        def in_cb(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            mic_q.put(np.copy(np.squeeze(indata)))

        in_kwargs: dict[str, Any] = {
            "samplerate": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
            "blocksize": int(SAMPLE_RATE * CHUNK_SECONDS),
            "callback": in_cb,
        }
        if self._input_device is not None:
            in_kwargs["device"] = self._input_device
        try:
            with sd.InputStream(**in_kwargs):
                outcome = self._watch_barge_in(mic_q, worker.is_alive)
                if outcome.interrupted:
                    self._tts_stop.set()
                return outcome
        except Exception as exc:  # noqa: BLE001
            logger.warning("SAPI barge-in mic failed: {}", exc)
            worker.join(timeout=45)
            if self._tts_stop.is_set():
                return SpeakOutcome(interrupted=True, reason="hotkey")
            return SpeakOutcome()
        finally:
            self._tts_stop.set()
            worker.join(timeout=8)

    def _sapi_say_chunks(self, text: str) -> None:
        """Speak SAPI in punctuation chunks so _tts_stop can cut remaining sentences."""
        parts = [p.strip() for p in re.split(r"(?<=[。！？.!?])", text) if p and p.strip()]
        if not parts:
            parts = [text]
        with self._tts_lock:
            for part in parts:
                if self._tts_stop.is_set():
                    return
                _sapi_say_once(part)


def _sapi_say_once(text: str) -> None:
    """One SAPI utterance with COM init on this thread."""
    engine = None
    try:
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001
            pass
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 185)
        _prefer_chinese_voice(engine)
        engine.say(text)
        engine.runAndWait()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS failed: {}", exc)
    finally:
        if engine is not None:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            import comtypes

            comtypes.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def _load_sensevoice(model_dir: Path) -> Any | None:
    """Load a local sherpa-onnx SenseVoice recognizer if model files exist."""
    found = find_sensevoice_files(model_dir)
    if found is None:
        logger.warning("SenseVoice model not found in {}. Run run.bat --download-models", model_dir)
        return None
    model_path, tokens_path = found
    try:
        import sherpa_onnx
    except Exception as exc:  # noqa: BLE001
        logger.warning("sherpa-onnx not installed: {}", exc)
        return None
    try:
        kwargs = {
            "model": str(model_path),
            "tokens": str(tokens_path),
            "num_threads": 4,
            "use_itn": True,
            "debug": False,
        }
        try:
            recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(**kwargs, language="zh")
        except TypeError:
            recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(**kwargs)
        logger.info("SenseVoice loaded from {}", model_path)
        return recognizer
    except Exception as exc:  # noqa: BLE001
        logger.warning("SenseVoice load failed: {}", exc)
        return None


def find_sensevoice_files(model_dir: Path) -> tuple[Path, Path] | None:
    """Locate model.int8.onnx (or model.onnx) plus tokens.txt under model_dir."""
    if not model_dir.exists():
        return None
    for name in ("model.int8.onnx", "model.onnx"):
        for model in model_dir.rglob(name):
            tokens = model.parent / "tokens.txt"
            if tokens.is_file():
                return model, tokens
    return None


def _load_whisper(model_size: str) -> Any | None:
    """Load faster-whisper from the local Hub cache only."""
    device = "cpu"
    compute = "int8"
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            device = "cuda"
            compute = "float16"
    except Exception:  # noqa: BLE001
        device = "cpu"
        compute = "int8"
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        logger.warning("Whisper not installed: {}", exc)
        return None
    candidates = [model_size]
    if model_size != "tiny":
        candidates.append("tiny")
    last_error: Exception | None = None
    for name in candidates:
        try:
            model = WhisperModel(name, device=device, compute_type=compute, local_files_only=True)
            logger.info("Whisper {} loaded from local cache on {}", name, device)
            return model
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    logger.warning("No local Whisper cache (wanted {}). {}", model_size, last_error)
    return None


def _transcribe_sensevoice(recognizer: Any, audio: np.ndarray) -> str:
    """Run sherpa-onnx SenseVoice on a 16 kHz float32 mono clip."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    stream = recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    recognizer.decode_stream(stream)
    raw = ""
    result = getattr(stream, "result", None)
    if result is not None:
        raw = str(getattr(result, "text", "") or "")
    return strip_sensevoice_tags(raw)


def strip_sensevoice_tags(text: str) -> str:
    """Remove SenseVoice language/emotion tags such as <|zh|>."""
    cleaned = _SENSEVOICE_TAG.sub("", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _select_input_device(devices: Any, default_in: Any) -> int | None:
    """Pick a real microphone, skipping mappers and stereo-mix loopbacks."""
    skip = ("mapper", "映射", "stereo mix", "立体声混音", "loopback", "what you hear")
    prefer = ("麦克风阵列", "mic array", "array", "麦克风", "microphone")
    ranked: list[tuple[int, int, str]] = []
    for index, item in enumerate(devices):
        channels = int(item.get("max_input_channels") or 0)
        if channels < 1:
            continue
        name = str(item.get("name") or "")
        lowered = name.lower()
        if any(token in lowered or token in name for token in skip):
            continue
        score = 0
        if default_in is not None and index == int(default_in):
            score += 3
        if any(token in lowered or token in name for token in prefer):
            score += 6
        ranked.append((score, index, name))
    if not ranked:
        return int(default_in) if default_in is not None else None
    ranked.sort(key=lambda row: (-row[0], row[1]))
    _, index, name = ranked[0]
    logger.info("Using microphone [{}] {}", index, name)
    return index


def _prefer_chinese_voice(engine: Any) -> str:
    """Select a Chinese SAPI voice when Windows has one installed."""
    try:
        voices = engine.getProperty("voices") or []
    except Exception:  # noqa: BLE001
        return ""
    keywords = ("huihui", "yaoyao", "kangkang", "zh-cn", "zh_cn", "chinese", "中文", "汉语")
    for voice in voices:
        blob = f"{getattr(voice, 'id', '')} {getattr(voice, 'name', '')}".lower()
        if any(key in blob for key in keywords):
            engine.setProperty("voice", voice.id)
            name = str(getattr(voice, "name", voice.id))
            logger.info("TTS voice {}", name)
            return name
    return ""


def _transcribe_google(audio: np.ndarray) -> str:
    """Recognize Chinese speech via Google's public Chromium endpoint."""
    import json

    import httpx

    pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    frames = (pcm * 32767.0).astype(np.int16).tobytes()
    try:
        response = httpx.post(
            "https://www.google.com/speech-api/v2/recognize",
            params={
                "client": "chromium",
                "lang": "zh-CN",
                "pFilter": "0",
                "key": "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw",
            },
            content=frames,
            headers={"Content-Type": f"audio/l16; rate={SAMPLE_RATE}"},
            timeout=15.0,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Google STT failed: {}", exc)
        return ""
    best = ""
    for line in response.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        for result in payload.get("result") or []:
            for alt in result.get("alternative") or []:
                transcript = str(alt.get("transcript") or "").strip()
                if transcript:
                    best = transcript
                    break
    return best


def _for_speech(text: str) -> str:
    """Strip markdown fences so TTS does not read backticks and code."""
    lines: list[str] = []
    in_fence = False
    for line in (text or "").splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        lines.append(line)
    spoken = " ".join(lines).strip()
    return spoken[:600]


def _rms(samples: np.ndarray) -> float:
    """Root-mean-square energy of a float32 clip."""
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    return float(np.sqrt(np.mean(np.square(data)) + 1e-9))


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear resample of a 1-D waveform."""
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if src_rate == dst_rate or data.size == 0:
        return data
    n = max(int(round(data.size * dst_rate / src_rate)), 1)
    old_t = np.linspace(0.0, 1.0, data.size, endpoint=False)
    new_t = np.linspace(0.0, 1.0, n, endpoint=False)
    return np.interp(new_t, old_t, data).astype(np.float32)


def _mp3_to_float32(data: bytes) -> tuple[np.ndarray, int] | None:
    """Decode MP3 bytes to mono float32 using PyAV (already a Whisper dependency)."""
    try:
        import av
    except Exception as exc:  # noqa: BLE001
        logger.warning("PyAV missing, cannot decode edge-tts mp3: {}", exc)
        return None
    try:
        container = av.open(io.BytesIO(data), format="mp3")
    except Exception:
        try:
            container = av.open(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            logger.warning("mp3 open failed: {}", exc)
            return None
    chunks: list[np.ndarray] = []
    sample_rate = SAMPLE_RATE
    try:
        stream = container.streams.audio[0]
        sample_rate = int(stream.rate or SAMPLE_RATE)
        resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        for frame in container.decode(audio=0):
            resampled = resampler.resample(frame)
            frames = resampled if isinstance(resampled, list) else [resampled]
            for item in frames:
                if item is None:
                    continue
                arr = item.to_ndarray()
                chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))
        flushed = []
        try:
            flushed = resampler.resample(None)
        except Exception:  # noqa: BLE001
            flushed = []
        extra = flushed if isinstance(flushed, list) else [flushed]
        for item in extra:
            if item is None:
                continue
            arr = item.to_ndarray()
            chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mp3 decode failed: {}", exc)
        return None
    finally:
        container.close()
    if not chunks:
        return None
    return np.concatenate(chunks), sample_rate


def stdin_lines(out_queue: queue.Queue[str], stop: threading.Event) -> None:
    """Background thread: push stdin lines into a queue until stop is set."""
    import sys

    while not stop.is_set():
        line = sys.stdin.readline()
        if line == "":
            break
        out_queue.put(line.rstrip("\n"))
