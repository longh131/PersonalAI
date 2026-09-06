"""Standby runtime: tap-to-talk session, silence endpointing, keyboard, TTS."""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from core.engine import PersonalAIEngine
from senses.hotkey import parse_hotkey as _parse_hotkey, start_hotkey_thread
from senses.presence import Presence
from senses.tray import TrayPresence
from senses.voice import SpeakOutcome, VoiceIO
from senses.wakeword import WakeWatcher
from tools.builtin import looks_like_image_path
from tools.desktop import foreground_is_meeting
from tools.health import HealthWatch, read_system_snapshot

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
_CHIME_KIND = {
    "reminder": "reminder",
    "alert": "alert",
    "job_done": "job",
    "briefing": "briefing",
}


def _banner(
    hotkey: str,
    silence: float,
    followup: float,
    *,
    wake: bool,
    name: str = "小派",
    codename: str = "Pai",
) -> str:
    """Build the idle help text from the current voice settings."""
    listen_hint = f"说「你好{name}」唤醒、" if wake else ""
    idle = "唤醒词+热键" if wake else "热键/命令"
    return f"""
============================================================
  Personal AI OS  ·  {name} / {codename}
  状态：待机（{idle}）
------------------------------------------------------------
  {listen_hint}/listen 或 {hotkey}   开始听；说完停顿约 {silence:.1f} 秒即提交
  回答后会再听 {followup:.0f} 秒，直接说即可，不必再按
  播报时可开口或按热键打断。超时回待机。说「退出」立即待机
  托盘图标颜色：蓝待机 / 绿聆听 / 黄思考 / 白播报
  打字回车只回文字。关掉程序请输入 /quit
  /shot     截屏并识别
  /quit     退出
============================================================
""".strip()


async def run_standby(engine: PersonalAIEngine, voice: VoiceIO) -> None:
    """Idle loop: keyboard, tray, wake word, screenshot, and voice sessions."""
    settings = engine.settings
    hotkey = (getattr(voice, "hotkey", None) or settings.voice_hotkey or "CTRL+SHIFT+L").upper()
    wake_on = bool(getattr(settings, "wakeword_enabled", True)) and voice.available
    tray_on = bool(getattr(settings, "tray_enabled", True))
    assistant_name = "小派"
    assistant_code = "Pai"
    try:
        ident = engine.identity.load().identity
        assistant_name = ident.name or "小派"
        assistant_code = ident.codename or "Pai"
    except Exception:  # noqa: BLE001
        assistant_name = "小派"
        assistant_code = "Pai"
    print(
        _banner(
            hotkey,
            settings.voice_silence_end,
            settings.voice_followup_seconds,
            wake=wake_on,
            name=assistant_name,
            codename=assistant_code,
        ),
        flush=True,
    )
    if voice.available:
        print(
            f"语音：热键 {hotkey}。识别：{getattr(voice, 'stt_label', '')}。",
            flush=True,
        )
    else:
        print("语音：未开启，只用键盘。", flush=True)
    if voice.tts_available:
        current = getattr(voice, "tts_voice_name", "") or "系统默认"
        print(f"播报：{current}。播报时开口或 {hotkey} 可打断。", flush=True)
    incoming: queue.Queue[tuple[str, str]] = queue.Queue()
    stop = threading.Event()
    wake: WakeWatcher | None = None
    tray: TrayPresence | None = None
    threading.Thread(target=stdin_lines_tagged, args=(incoming, stop), daemon=True).start()
    threading.Thread(
        target=start_hotkey_thread,
        args=(incoming, stop, hotkey),
        daemon=True,
        name="hotkey",
    ).start()
    if tray_on:
        tray = TrayPresence(incoming, stop, name=assistant_name, codename=assistant_code)
        tray.start()
        print("托盘：已驻留。可隐藏控制台或设置开机启动。", flush=True)
    presence = Presence(assistant_name, tray=tray)
    presence.set("idle")
    reminder_task = asyncio.create_task(
        _reminder_pump(getattr(engine.memory, "reminders", None), incoming, stop)
    )
    health_task = asyncio.create_task(_health_pump(incoming, stop))
    job_task = asyncio.create_task(_job_worker(engine, incoming, stop))
    brief_task = asyncio.create_task(_startup_brief(engine, incoming, stop))
    if wake_on:
        def _on_wake(rest: str) -> None:
            incoming.put(("wake", rest.strip() or "/listen"))

        wake = WakeWatcher(
            voice,
            name=assistant_name,
            on_wake=_on_wake,
            stop=stop,
            settings=settings,
        )
        wake.start()
        print(
            f"唤醒词：默认开。说「你好{assistant_name}」或「嗨{assistant_name}」进入聆听"
            f"（只说「你好」不会进）。热键、/listen、托盘「开始听」也可。"
            f"也可连着说「你好{assistant_name}，打开记事本」。",
            flush=True,
        )
    print(
        f"\n{assistant_name}：我在。要说话请唤醒、按 {hotkey}，或打字。\n",
        flush=True,
    )
    try:
        while not stop.is_set():
            event = await _queue_get(incoming)
            if event is None:
                continue
            _source, payload = event
            if _source in _CHIME_KIND:
                await _deliver_chime(
                    engine, presence, voice, payload, kind=_CHIME_KIND[_source], resume="idle"
                )
                continue
            text = payload.strip()
            if not text:
                continue
            if text.lower() in QUIT_APP_COMMANDS:
                print(f"{assistant_name}：好，我先待机结束。", flush=True)
                break
            if _is_end_session(text):
                print("已待机。", flush=True)
                presence.set("idle")
                continue
            if text.lower() in SHOT_COMMANDS:
                await _handle_shot(engine, voice, presence)
                presence.set("idle")
                continue
            if _source == "wake" and text.lower() not in LISTEN_COMMANDS:
                print("唤醒。", flush=True)
                logger.info("wake command {}", text)
                if wake:
                    wake.pause()
                try:
                    outcome = await _voice_session(
                        engine, voice, incoming, stop, presence, first_heard=text
                    )
                finally:
                    if wake:
                        wake.resume()
                    presence.set("idle")
                if outcome == "quit":
                    break
                continue
            if text.lower() in LISTEN_COMMANDS or _source in {"hotkey", "tray", "wake"}:
                print("收到，开始听。", flush=True)
                logger.info("listen command accepted source={}", _source)
                if wake:
                    wake.pause()
                try:
                    outcome = await _voice_session(engine, voice, incoming, stop, presence)
                finally:
                    if wake:
                        wake.resume()
                    presence.set("idle")
                if outcome == "quit":
                    break
                continue
            if looks_like_image_path(text):
                await _handle_image_path(engine, voice, text, presence)
                presence.set("idle")
                continue
            await _handle_user_turn(engine, voice, text, input_type="text", speak=False, presence=presence)
            presence.set("idle")
    finally:
        stop.set()
        for task in (reminder_task, health_task, job_task, brief_task):
            task.cancel()
        for task in (reminder_task, health_task, job_task, brief_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if wake:
            wake.pause()
        if tray:
            tray.stop_icon()


async def _voice_session(
    engine: PersonalAIEngine,
    voice: VoiceIO,
    incoming: queue.Queue[tuple[str, str]],
    stop: threading.Event,
    presence: Presence,
    *,
    first_heard: str | None = None,
) -> str | None:
    """One conversation: first listen, then follow-up windows until silence timeout."""
    if not voice.available:
        print("麦克风不可用。", flush=True)
        return None
    settings = engine.settings
    first = True
    pending_heard = (first_heard or "").strip()
    while not stop.is_set():
        if pending_heard:
            heard = pending_heard
            pending_heard = None
            action = await _voice_reply_loop(engine, voice, heard, incoming, stop, presence)
            if action == "quit":
                return "quit"
            if action == "standby":
                return None
            if action == "restart":
                first = True
                continue
            first = False
            continue
        wait = settings.voice_start_timeout if first else settings.voice_followup_seconds
        presence.set("listening")
        if first:
            print(
                f"请说话，说完停顿约 {settings.voice_silence_end:.1f} 秒。",
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
            if _source in _CHIME_KIND:
                await _cancel_listen(voice, listen_task)
                await _deliver_chime(
                    engine, presence, voice, payload, kind=_CHIME_KIND[_source], resume="listening"
                )
                first = False
                skip_heard = True
                break
            text = payload.strip()
            if not text:
                continue
            if text.lower() in QUIT_APP_COMMANDS:
                await _cancel_listen(voice, listen_task)
                print(f"{presence.name}：好，我先待机结束。", flush=True)
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
                await _handle_shot(engine, voice, presence)
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
                await _handle_image_path(engine, voice, typed, presence)
            else:
                await _handle_user_turn(
                    engine, voice, typed, input_type="text", speak=False, presence=presence
                )
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
        action = await _voice_reply_loop(engine, voice, heard, incoming, stop, presence)
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
    presence: Presence,
) -> str | None:
    """Process a spoken turn and any barge-in follow-ups until playback finishes."""
    current = heard
    while current:
        print(f"你：{current}", flush=True)
        if _is_end_session(current):
            print("已待机。", flush=True)
            if voice.tts_available:
                presence.set("speaking")
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
            presence=presence,
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
                await _handle_image_path(engine, voice, typed, presence)
            else:
                await _handle_user_turn(
                    engine, voice, typed, input_type="text", speak=False, presence=presence
                )
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
    presence: Presence | None = None,
) -> SpeakOutcome | str | tuple[str, str] | None:
    """Run the engine. Speak only for voice-originated turns."""
    if presence is not None:
        presence.set("thinking")
    lock = getattr(engine, "_turn_lock", None)
    if lock is not None and lock.locked():
        print("…上一件还在做，这个问题排队", flush=True)

    async def _on_progress(_name: str, detail: str) -> None:
        print(f"…{detail}", flush=True)

    try:
        reply = await engine.process(
            text,
            input_type=input_type,  # type: ignore[arg-type]
            on_progress=_on_progress,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("process failed")
        reply = f"处理失败：{exc}"
    return await _emit(
        reply, voice, speak=speak, incoming=incoming, stop=stop, presence=presence, engine=engine
    )


async def _handle_shot(engine: PersonalAIEngine, voice: VoiceIO, presence: Presence | None = None) -> None:
    """Capture the screen and ask the engine to describe it."""
    if presence is not None:
        presence.set("thinking")
    print("…截屏识别中", flush=True)
    try:
        path = await engine.vision.capture_screen()
        reply = await engine.process("请识别并总结这张截屏。", input_type="image", image_path=str(path))
    except Exception as exc:  # noqa: BLE001
        reply = f"截屏失败：{exc}"
    await _emit(reply, voice, speak=False, presence=presence, engine=engine)


async def _handle_image_path(
    engine: PersonalAIEngine, voice: VoiceIO, text: str, presence: Presence | None = None
) -> None:
    """Analyze a user-supplied image path."""
    path = Path(text.strip().strip('"'))
    if presence is not None:
        presence.set("thinking")
    print(f"…扫描图片 {path}", flush=True)
    reply = await engine.process(f"请识别这张图：{path}", input_type="image", image_path=str(path))
    await _emit(reply, voice, speak=False, presence=presence, engine=engine)


async def _queue_get(incoming: queue.Queue[tuple[str, str]]) -> tuple[str, str] | None:
    """Non-blocking queue poll that yields to the event loop."""
    try:
        return incoming.get_nowait()
    except queue.Empty:
        await asyncio.sleep(0.05)
        return None


async def _reminder_pump(store: Any, incoming: queue.Queue[tuple[str, str]], stop: threading.Event) -> None:
    """Claim due reminders and push them onto the standby event queue."""
    if store is None:
        return
    while not stop.is_set():
        try:
            due = await store.claim_due()
            for item in due:
                incoming.put(
                    (
                        "reminder",
                        json.dumps({"id": item.id, "message": item.message}, ensure_ascii=False),
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("reminder pump: {}", exc)
        await asyncio.sleep(0.4)


def _reminder_text(payload: str) -> str:
    raw = (payload or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return str(data.get("message") or raw)
        except json.JSONDecodeError:
            return raw
    return raw


async def _may_speak(engine: PersonalAIEngine | None, kind: str) -> tuple[bool, str]:
    """Consult protocol store + foreground meeting window."""
    store = getattr(getattr(engine, "memory", None), "protocols", None) if engine else None
    if store is None:
        return True, ""
    meeting = False
    try:
        meeting = await asyncio.to_thread(foreground_is_meeting)
    except Exception:  # noqa: BLE001
        meeting = False
    return await store.may_speak(kind, meeting=meeting)


async def _deliver_chime(
    engine: PersonalAIEngine,
    presence: Presence,
    voice: VoiceIO,
    payload: str,
    *,
    kind: str,
    resume: str,
) -> None:
    """Speak a reminder or health alert unless a protocol forbids TTS."""
    message = _reminder_text(payload)
    tag = {"reminder": "提醒", "alert": "舰况", "job": "后台", "briefing": "简报"}.get(kind, "提醒")
    print(f"\n{presence.name}（{tag}）：{message}\n", flush=True)
    if presence.tray is not None:
        presence.tray.notify(presence.name, message)
    allowed, reason = await _may_speak(engine, kind)
    if not allowed:
        print(f"（协议：{reason}，只显示不播报）", flush=True)
        presence.set(resume)
        return
    presence.set("speaking")
    prefix = "提醒。" if kind == "reminder" else ""
    if getattr(voice, "tts_available", False):
        try:
            await voice.speak(f"{prefix}{message}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("{} speak failed: {}", kind, exc)
    presence.set(resume)


async def _health_pump(incoming: queue.Queue[tuple[str, str]], stop: threading.Event) -> None:
    """Poll laptop status; only enqueue rare anomaly alerts."""
    watch = HealthWatch()
    while not stop.is_set():
        try:
            snap = await asyncio.to_thread(read_system_snapshot)
            for message in watch.alerts(snap):
                incoming.put(("alert", message))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("health pump: {}", exc)
        for _ in range(90):
            if stop.is_set():
                return
            await asyncio.sleep(0.5)


async def _job_worker(
    engine: PersonalAIEngine, incoming: queue.Queue[tuple[str, str]], stop: threading.Event
) -> None:
    """Run queued background instructions without blocking the listen loop's submit path."""
    jobs = getattr(engine, "jobs", None)
    if jobs is None:
        return
    while not stop.is_set():
        item = jobs.pop_nowait()
        if not item:
            await asyncio.sleep(0.25)
            continue
        jobs.current = item
        shown = item if len(item) <= 40 else item[:40] + "…"
        print(f"…后台任务开始：{shown}", flush=True)
        try:
            reply = await engine.process(f"[后台]{item}", input_type="text")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("background job failed: {}", exc)
            reply = f"后台任务失败：{exc}"
        jobs.current = ""
        text = (reply or "").strip()
        if len(text) > 200:
            text = text[:200] + "…"
        incoming.put(("job_done", json.dumps({"message": f"后台完成。{text}"}, ensure_ascii=False)))


async def _startup_brief(
    engine: PersonalAIEngine, incoming: queue.Queue[tuple[str, str]], stop: threading.Event
) -> None:
    """Speak one morning or leaving briefing per day in the matching time window."""
    await asyncio.sleep(1.5)
    if stop.is_set():
        return
    from datetime import datetime

    from tools.briefing import auto_briefing_kind, compose_briefing

    kind = auto_briefing_kind()
    if not kind:
        return
    today = datetime.now().astimezone().date().isoformat()
    key = f"brief:{kind}:{today}"
    try:
        if await engine.memory.kv_get(key):
            return
        reminders = []
        store = getattr(engine.memory, "reminders", None)
        if store is not None:
            reminders = await store.list_pending()
        text = await compose_briefing(kind, reminders=reminders)
        await engine.memory.kv_set(key, "1")
        incoming.put(("briefing", text))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup briefing: {}", exc)


async def _emit(
    reply: str,
    voice: VoiceIO,
    *,
    speak: bool,
    incoming: queue.Queue[tuple[str, str]] | None = None,
    stop: threading.Event | None = None,
    presence: Presence | None = None,
    engine: PersonalAIEngine | None = None,
) -> SpeakOutcome | str | tuple[str, str] | None:
    """Print a reply; speak only when this turn came from the microphone."""
    name = presence.name if presence is not None else "小派"
    print(f"\n{name}：{reply}\n", flush=True)
    if not speak:
        return None
    allowed, reason = await _may_speak(engine, "reply")
    if not allowed:
        print(f"（协议：{reason}，只显示不播报）", flush=True)
        if presence is not None and presence.tray is not None:
            presence.tray.notify(presence.name, reply[:80])
        return SpeakOutcome()
    if presence is not None:
        presence.set("speaking")
    print("开口或热键可打断…", flush=True)
    speak_task = asyncio.create_task(voice.speak_with_barge_in(reply))
    interrupt: SpeakOutcome | str | tuple[str, str] | None = None
    pending_chimes: list[tuple[str, str]] = []
    while not speak_task.done():
        if stop is not None and stop.is_set():
            voice.interrupt_speech()
            interrupt = "quit"
            break
        event = await _queue_get(incoming) if incoming is not None else None
        if event is None:
            continue
        _source, payload = event
        if _source in _CHIME_KIND:
            pending_chimes.append((_source, payload))
            continue
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
        if text.lower() in LISTEN_COMMANDS or _source in {"hotkey", "tray", "wake"}:
            interrupt = "restart"
        else:
            interrupt = ("typed", text)
        break
    try:
        outcome = await speak_task
    except Exception as exc:  # noqa: BLE001
        logger.warning("speak failed: {}", exc)
        outcome = SpeakOutcome()
    if pending_chimes and interrupt is None and presence is not None and engine is not None:
        for source, payload in pending_chimes:
            await _deliver_chime(
                engine, presence, voice, payload, kind=_CHIME_KIND[source], resume="listening"
            )
    if interrupt == "quit":
        print(f"{name}：好，我先待机结束。", flush=True)
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
