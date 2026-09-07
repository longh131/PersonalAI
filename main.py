"""Personal AI OS entry: one-click standby with voice, vision and text."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from config.hub import configure_huggingface_hub
from config.settings import PROJECT_ROOT, load_settings
from core.engine import PersonalAIEngine
from senses.standby import run_standby
from senses.vision import VisionIO
from senses.voice import VoiceIO


def _prepare_stdio() -> None:
    """Force UTF-8 on Windows consoles so Chinese logs and replies render."""
    os.environ.setdefault("PYTHONUTF8", "1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def _upsert_env(path: Path, key: str, value: str) -> None:
    """Create or replace a KEY=value line in `.env` without touching other keys."""
    lines: list[str] = []
    found = False
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_settings(*, require_llm_key: bool = True):
    """Guarantee `.env` exists and contains a DeepSeek key when that provider is used.

    Returns:
        Loaded settings. May prompt on stdin for a missing API key.
    """
    env_path = PROJECT_ROOT / ".env"
    template = PROJECT_ROOT / ".env.template"
    if not env_path.exists() and template.exists():
        env_path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已创建 {env_path} ，请填入密钥。")
    load_dotenv(env_path, override=False)
    settings = load_settings()
    provider = (settings.llm_provider or "deepseek").lower()
    if require_llm_key and provider == "deepseek" and not settings.deepseek_api_key.strip():
        print("未检测到 DEEPSEEK_API_KEY。")
        print("在 https://platform.deepseek.com 创建密钥后粘贴到这里。")
        try:
            key = input("DeepSeek API Key（留空退出）: ").strip()
        except EOFError:
            key = ""
        if not key:
            print("没有密钥无法对话。把密钥写入 .env 后重新运行 run.bat。")
            sys.exit(1)
        _upsert_env(env_path, "DEEPSEEK_API_KEY", key)
        load_dotenv(env_path, override=True)
        settings = load_settings()
    return settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI flags for standby, text-only, and self-check modes."""
    parser = argparse.ArgumentParser(description="Personal AI OS")
    parser.add_argument("--text-only", action="store_true", help="不加载麦克风和语音识别，只用键盘")
    parser.add_argument("--no-speak", action="store_true", help="不语音播报")
    parser.add_argument("--self-check", action="store_true", help="初始化后打印健康检查并退出")
    parser.add_argument(
        "--download-models",
        action="store_true",
        help="一次性下载 SenseVoice 和向量模型到本机后退出",
    )
    return parser.parse_args(argv)


SENSEVOICE_URLS = [
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
    "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/resolve/main/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
]


def download_models(settings) -> int:
    """Fetch SenseVoice and the embedding model into the local project cache."""
    from senses.voice import find_sensevoice_files

    print("一次性下载：请保持网络畅通（GitHub 或 Hugging Face）。", flush=True)
    dest = Path(settings.sensevoice_dir)
    if not dest.is_absolute():
        dest = PROJECT_ROOT / dest
    print("1/2 SenseVoice（本地中文识别）…", flush=True)
    if find_sensevoice_files(dest) is not None:
        print(f"    已就绪：{dest}", flush=True)
    elif not _download_sensevoice(dest):
        print("    SenseVoice 下载失败。可科学上网后重试 run.bat --download-models", flush=True)
        return 1
    print(f"2/2 向量模型 {settings.embedding_model} …", flush=True)
    try:
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(settings.embedding_model, local_files_only=False)
        print("    向量模型已就绪。", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"    向量模型下载失败：{exc}", flush=True)
        return 1
    print("下载完成。可以再双击 run.bat 正常使用。", flush=True)
    return 0


def _download_sensevoice(dest: Path) -> bool:
    """Download and extract the sherpa-onnx SenseVoice int8 archive."""
    import tarfile

    import httpx

    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "sensevoice.tar.bz2"
    last_error = ""
    for url in SENSEVOICE_URLS:
        print(f"    尝试 {url}", flush=True)
        try:
            with httpx.Client(follow_redirects=True, timeout=180.0) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with archive.open("wb") as handle:
                        total = 0
                        last_print = 0
                        for chunk in response.iter_bytes(1024 * 64):
                            handle.write(chunk)
                            total += len(chunk)
                            if total - last_print >= 10 * 1024 * 1024:
                                print(f"    已下载 {total / 1024 / 1024:.0f} MB", flush=True)
                                last_print = total
                    print(f"    下载完成 {total / 1024 / 1024:.0f} MB", flush=True)
            last_error = ""
            break
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            print(f"    失败：{exc}", flush=True)
            try:
                archive.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    if last_error and not archive.is_file():
        return False
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(dest, filter="data")
    except TypeError:
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(dest)
    except Exception as exc:  # noqa: BLE001
        print(f"    解压失败：{exc}", flush=True)
        return False
    finally:
        try:
            archive.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    from senses.voice import find_sensevoice_files

    found = find_sensevoice_files(dest)
    if found is None:
        print("    解压后未找到 model.int8.onnx / tokens.txt", flush=True)
        return False
    print(f"    SenseVoice 已就绪：{found[0]}", flush=True)
    return True


async def async_main(args: argparse.Namespace) -> int:
    """Boot the engine and either self-check or enter standby."""
    settings = ensure_settings()
    if args.text_only:
        settings.voice_enabled = False
    if args.no_speak:
        settings.tts_enabled = False
    vision = VisionIO(enabled=settings.vision_enabled)
    voice = VoiceIO(
        enabled=settings.voice_enabled,
        tts_enabled=settings.tts_enabled,
        model_size=settings.stt_model,
        settings=settings,
    )
    engine = PersonalAIEngine(settings, vision=vision)
    print("正在启动 Personal AI OS（使用本机缓存模型，不在启动时下载）…", flush=True)
    await vision.initialize()
    await voice.initialize()
    await engine.initialize()
    health = await engine.self_check()
    print(
        f"内核就绪  session={health['session_id'][:8]}  "
        f"工具={sum(1 for ok in health['tools'].values() if ok)}/4  "
        f"资料库={health.get('library', '')}",
        flush=True,
    )
    if args.self_check:
        print(health)
        await engine.shutdown()
        return 0
    try:
        await run_standby(engine, voice)
    finally:
        await engine.shutdown()
    return 0


def main() -> None:
    """Synchronous CLI wrapper."""
    _prepare_stdio()
    args = parse_args()
    configure_huggingface_hub(offline=not args.download_models)
    try:
        if args.download_models:
            settings = ensure_settings(require_llm_key=False)
            raise SystemExit(download_models(settings))
        raise SystemExit(asyncio.run(async_main(args)))
    except KeyboardInterrupt:
        print("\n已中断。", flush=True)
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
