"""Standby runtime: tap-to-talk session, silence endpointing, keyboard, TTS."""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
from pathlib import Path

from loguru import logger

from core.engine import PersonalAIEngine
from senses.voice import SpeakOutcome, VoiceIO
from tools.builtin import looks_like_image_path

LISTEN_COMMANDS = {"/listen", "听我说"}
QUIT_APP_COMMANDS = {"/quit", "/exit"}
END_SESSION_COMMANDS = {
    "退出",
    "待机",
    "结束",
    "结束对话",
    "不用了",
    "没事了",
    "先这样",
    "停止",
    "停止聆听",
    "回去待机",
}
SHOT_COMMANDS = {"/shot", "/screen", "截屏", "截图"}

_VK_KEYS = {
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "L": 0x4C,
    "M": 0x4D,
    "K": 0x4B,
    "SPACE": 0x20,
    "SPACEBAR": 0x20,
}
_VK_MODS = {
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "MENU": 0x12,
}


def _banner(hotkey: str, silence: float, followup: float) -> str:
    """Build the idle help text from the current voice settings."""
    return f"""
============================================================
  Personal AI OS  ·  织 / Aion
  状态：待机（不录音）
------------------------------------------------------------
  /listen 或 {hotkey}   开始听；说完停顿约 {silence:.1f} 秒即提交
  回答后会再听 {followup:.0f} 秒，直接说即可，不必再按
  播报时可开口或按热键打断。超时回待机。说「退出」立即待机
  打字回车只回文字。关掉程序请输入 /quit
  /shot     截屏并识别
  /quit     退出
============================================================
""".strip()


async def run_standby(engine: PersonalAIEngine, voice: VoiceIO) -> None:
    """Idle loop: keyboard, screenshot, and explicit voice-session starts."""
    settings = engine.settings
    hotkey = (getattr(voice, "hotkey", None) or settings.voice_hotkey or "CTRL+SHIFT+L").upper()
    print(_banner(hotkey, settings.voice_silence_end, settings.voice_followup_seconds), flush=True)
    if voice.available:
        print(
            f"语音：点一下开始。热键 {hotkey}。识别：{getattr(voice, 'stt_label', '')}。",
            flush=True,
        )
    else:
        print("语音：未开启，只用键盘。", flush=True)
    if voice.tts_available:
        current = getattr(voice, "tts_voice_name", "") or "系统默认"
        print(f"播报：{current}。播报时开口或 {hotkey} 可打断。", flush=True)
        catalog = getattr(voice, "tts_voices", []) or []
        if catalog:
            listed = "；".join(f"{i + 1}) {name}" for i, name in enumerate(catalog[:6]))
            print(f"可选声音：{listed}", flush=True)
    incoming: queue.Queue[tuple[str, str]] = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=stdin_lines_tagged, args=(incoming, stop), daemon=True).start()
    threading.Thread(
        target=_hotkey_loop,
        args=(incoming, stop, hotkey),
        daemon=True,
    ).start()
    print("\n织：我在。要说话请 /listen 或按 {0}。打字则只回文字。\n".format(hotkey), flush=True)
    try:
        while not stop.is_set():
            event = await _queue_get(incoming)
            if event is None:
                continue
            _source, payload = event
            text = payload.strip()
            if not text:
                continue
            if text.lower() in QUIT_APP_COMMANDS:
                print("织：好，我先待机结束。", flush=True)
                break
            if _is_end_session(text):
                print("已待机。", flush=True)
                continue
            if text.lower() in SHOT_COMMANDS:
                await _handle_shot(engine, voice)
                continue
            if text.lower() in LISTEN_COMMANDS:
                print("收到 /listen，开始听。", flush=True)
                logger.info("listen command accepted source={}", _source)
                outcome = await _voice_session(engine, voice, incoming, stop)
                if outcome == "quit":
                    break
                continue
            if looks_like_image_path(text):
                await _handle_image_path(engine, voice, text)
                continue
            await _handle_user_turn(engine, voice, text, input_type="text", speak=False)
    finally:
        stop.set()


async def _voice_session(
    engine: PersonalAIEngine,
    voice: VoiceIO,
    incoming: queue.Queue[tuple[str, str]],
    stop: threading.Event,
) -> str | None:
    """One conversation: first listen, then follow-up windows until silence timeout."""
    if not voice.available:
        print("麦克风不可用。", flush=True)
        return None
    settings = engine.settings
    first = True
    while not stop.is_set():
        wait = settings.voice_start_timeout if first else settings.voice_followup_seconds
        if first:
            print(
                f"聆听中… 请说话，说完停顿约 {settings.voice_silence_end:.1f} 秒。",
                flush=True,
            )
        else:
            print(
                f"继续说即可（{wait:.0f} 秒内），或打字回车。超时将待机。",
                flush=True,
            )
        listen_task = asyncio.create_task(voice.listen_turn(wait_for_start=wait))
        typed: str | None = None
        restart = False
        skip_heard = False
        while not listen_task.done():
            if stop.is_set():
                voice.abort()
                await _cancel_listen(voice, listen_task)
                return "quit"
            event = await _queue_get(incoming)
            if event is None:
                continue
            _source, payload = event
            text = payload.strip()
            if not text:
                continue
            if text.lower() in QUIT_APP_COMMANDS:
                await _cancel_listen(voice, listen_task)
                print("织：好，我先待机结束。", flush=True)
                return "quit"
            if _is_end_session(text):
                await _cancel_listen(voice, listen_task)
                print("已待机。", flush=True)
                return None
            if text.lower() in LISTEN_COMMANDS:
                await _cancel_listen(voice, listen_task)
                restart = True
                break
            if text.lower() in SHOT_COMMANDS:
                await _cancel_listen(voice, listen_task)
                await _handle_shot(engine, voice)
                first = False
                skip_heard = True
                break
            await _cancel_listen(voice, listen_task)
            typed = text
            break
        if restart:
            first = True
            continue
        if skip_heard:
            continue
        if typed is not None:
            if looks_like_image_path(typed):
                await _handle_image_path(engine, voice, typed)
            else:
                await _handle_user_turn(engine, voice, typed, input_type="text", speak=False)
            first = False
            continue
        heard = ""
        if listen_task.done() and not listen_task.cancelled():
            try:
                heard = listen_task.result() or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("listen_turn failed: {}", exc)
                heard = ""
        else:
            await _cancel_listen(voice, listen_task)
        if not heard:
            print("已待机。再说请 /listen 或热键。", flush=True)
            return None
        action = await _voice_reply_loop(engine, voice, heard, incoming, stop)
        if action == "quit":
            return "quit"
        if action == "standby":
            return None
        if action == "restart":
            first = True
            continue
        first = False
    return None


async def _voice_reply_loop(
    engine: PersonalAIEngine,
    voice: VoiceIO,
    heard: str,
    incoming: queue.Queue[tuple[str, str]],
    stop: threading.Event,
) -> str | None:
    """Process a spoken turn and any barge-in follow-ups until playback finishes."""
    current = heard
    while current:
        print(f"你：{current}", flush=True)
        if _is_end_session(current):
            print("已待机。", flush=True)
            if voice.tts_available:
                await voice.speak("好，已待机。")
            return "standby"
        result = await _handle_user_turn(
            engine,
            voice,
            current,
            input_type="voice",
            speak=True,
            incoming=incoming,
            stop=stop,
        )
        if result == "quit":
            return "quit"
        if result == "standby":
            return "standby"
        if result == "restart":
            return "restart"
        if isinstance(result, tuple) and result[0] == "typed":
            typed = result[1]
            if looks_like_image_path(typed):
                await _handle_image_path(engine, voice, typed)
            else:
                await _handle_user_turn(engine, voice, typed, input_type="text", speak=False)
            return None
        if isinstance(result, SpeakOutcome) and result.reason == "barge_in" and result.audio is not None:
            current = await asyncio.to_thread(voice.transcribe_audio, result.audio)
            if not current:
                print("（打断后没听清，继续说即可）", flush=True)
                return None
            continue
        if isinstance(result, SpeakOutcome) and result.reason == "hotkey":
            return "restart"
        return None
    return None


async def _cancel_listen(voice: VoiceIO, listen_task: asyncio.Task[str]) -> None:
    """Stop the recorder thread and absorb the cancelled task."""
    voice.abort()
    if not listen_task.done():
        listen_task.cancel()
    try:
        await listen_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        return


async def _handle_user_turn(
    engine: PersonalAIEngine,
    voice: VoiceIO,
    text: str,
    *,
    input_type: str,
    speak: bool,
    incoming: queue.Queue[tuple[str, str]] | None = None,
    stop: threading.Event | None = None,
) -> SpeakOutcome | str | tuple[str, str] | None:
    """Run the engine. Speak only for voice-originated turns."""
    print("…思考中", flush=True)
    try:
        reply = await engine.process(text, input_type=input_type)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.exception("process failed")
        reply = f"处理失败：{exc}"
    return await _emit(reply, voice, speak=speak, incoming=incoming, stop=stop)


async def _handle_shot(engine: PersonalAIEngine, voice: VoiceIO) -> None:
    """Capture the screen and ask the engine to describe it."""
    print("…截屏识别中", flush=True)
    try:
        path = await engine.vision.capture_screen()
        reply = await engine.process("请识别并总结这张截屏。", input_type="image", image_path=str(path))
    except Exception as exc:  # noqa: BLE001
        reply = f"截屏失败：{exc}"
    await _emit(reply, voice, speak=False)


async def _handle_image_path(engine: PersonalAIEngine, voice: VoiceIO, text: str) -> None:
    """Analyze a user-supplied image path."""
    path = Path(text.strip().strip('"'))
    print(f"…扫描图片 {path}", flush=True)
    reply = await engine.process(f"请识别这张图：{path}", input_type="image", image_path=str(path))
    await _emit(reply, voice, speak=False)


async def _queue_get(incoming: queue.Queue[tuple[str, str]]) -> tuple[str, str] | None:
    """Non-blocking queue poll that yields to the event loop."""
    try:
        return incoming.get_nowait()
    except queue.Empty:
        await asyncio.sleep(0.05)
        return None


async def _emit(
    reply: str,
    voice: VoiceIO,
    *,
    speak: bool,
    incoming: queue.Queue[tuple[str, str]] | None = None,
    stop: threading.Event | None = None,
) -> SpeakOutcome | str | tuple[str, str] | None:
    """Print a reply; speak only when this turn came from the microphone."""
    print(f"\n织：{reply}\n", flush=True)
    if not speak:
        return None
    print("播报中（开口或热键可打断）…", flush=True)
    speak_task = asyncio.create_task(voice.speak_with_barge_in(reply))
    interrupt: SpeakOutcome | str | tuple[str, str] | None = None
    while not speak_task.done():
        if stop is not None and stop.is_set():
            voice.interrupt_speech()
            interrupt = "quit"
            break
        event = await _queue_get(incoming) if incoming is not None else None
        if event is None:
            continue
        _source, payload = event
        text = payload.strip()
        if not text:
            continue
        if text.lower() in QUIT_APP_COMMANDS:
            voice.interrupt_speech()
            interrupt = "quit"
            break
        if _is_end_session(text):
            voice.interrupt_speech()
            print("已待机。", flush=True)
            interrupt = "standby"
            break
        voice.interrupt_speech()
        if text.lower() in LISTEN_COMMANDS or _source == "hotkey":
            interrupt = "restart"
        else:
            interrupt = ("typed", text)
        break
    try:
        outcome = await speak_task
    except Exception as exc:  # noqa: BLE001
        logger.warning("speak failed: {}", exc)
        outcome = SpeakOutcome()
    if interrupt == "quit":
        print("织：好，我先待机结束。", flush=True)
        return "quit"
    if interrupt == "standby":
        return "standby"
    if interrupt == "restart":
        return "restart"
    if isinstance(interrupt, tuple):
        return interrupt
    return outcome


_SESSION_PARTICLES = ("了", "吧", "啊", "呀", "哦", "呢", "啦")
_NOT_STANDBY_HINTS = ("怎么", "如何", "为什么", "登录", "程序", "软件", "不要")


def _is_end_session(text: str) -> bool:
    """Return True when the user wants to leave the voice session, not the app."""
    cleaned = (text or "").strip().strip("。.!！，,？?、 ")
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if cleaned in END_SESSION_COMMANDS or lowered in END_SESSION_COMMANDS:
        return True
    compact = cleaned
    for suffix in _SESSION_PARTICLES:
        if compact.endswith(suffix) and len(compact) > len(suffix):
            compact = compact[: -len(suffix)]
            break
    if compact in END_SESSION_COMMANDS:
        return True
    if any(hint in cleaned for hint in _NOT_STANDBY_HINTS):
        return False
    if "退出" in cleaned and len(cleaned) <= 8:
        return True
    return False


def stdin_lines_tagged(incoming: queue.Queue[tuple[str, str]], stop: threading.Event) -> None:
    """Push typed lines as ('kbd', text)."""
    while not stop.is_set():
        line = sys.stdin.readline()
        if line == "":
            incoming.put(("kbd", "/quit"))
            break
        incoming.put(("kbd", line.rstrip("\n")))


def _parse_hotkey(spec: str) -> tuple[list[int], int] | None:
    """Parse 'CTRL+SHIFT+L' into modifier VKs and the main key VK."""
    parts = [p.strip().upper() for p in (spec or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    main = parts[-1]
    mods = parts[:-1]
    main_vk = _VK_KEYS.get(main)
    if main_vk is None and len(main) == 1 and "A" <= main <= "Z":
        main_vk = ord(main)
    if main_vk is None:
        return None
    mod_vks: list[int] = []
    for mod in mods:
        vk = _VK_MODS.get(mod)
        if vk is None:
            return None
        mod_vks.append(vk)
    return mod_vks, main_vk


def _hotkey_loop(incoming: queue.Queue[tuple[str, str]], stop: threading.Event, hotkey: str) -> None:
    """Listen for a chord such as Ctrl+Shift+L and inject /listen."""
    parsed = _parse_hotkey(hotkey)
    if parsed is None or sys.platform != "win32":
        logger.warning("Hotkey '{}' not usable on this platform", hotkey)
        return
    mod_vks, main_vk = parsed
    try:
        user32 = __import__("ctypes").windll.user32
    except Exception:  # noqa: BLE001
        logger.warning("Hotkey unavailable")
        return
    last = 0.0
    while not stop.is_set():
        try:
            mods_down = all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in mod_vks) if mod_vks else True
            main_down = bool(user32.GetAsyncKeyState(main_vk) & 0x8000)
        except Exception:  # noqa: BLE001
            return
        now = time.monotonic()
        if mods_down and main_down and (now - last) > 0.7:
            last = now
            incoming.put(("hotkey", "/listen"))
        time.sleep(0.05)
