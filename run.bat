@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python。请先安装 Python 3.11 或 3.12（推荐），并勾选 Add to PATH。
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
python -m pip install -U pip

echo [2/4] 安装核心依赖...
python -m pip install langgraph langchain-core openai pydantic pydantic-settings python-dotenv pyyaml loguru httpx aiosqlite numpy pytest pytest-asyncio
if errorlevel 1 (
    echo 核心依赖安装失败。请把完整报错发给我。
    pause
    exit /b 1
)

echo [3/4] 安装语音 / 截屏 / 搜索依赖（失败也不影响键盘对话）...
python -m pip install ddgs pillow mss pyttsx3 sounddevice edge-tts sherpa-onnx pystray
python -m pip install sentence-transformers rapidocr-onnxruntime
if errorlevel 1 (
    echo 向量/OCR 安装失败：仍可聊天，语义记忆会降级。推荐 Python 3.11 或 3.12。
)
python -m pip install faster-whisper
if errorlevel 1 (
    echo Whisper 安装失败：将只用键盘输入。可加 --text-only 启动。
)

echo [4/4] 进入待机。说话或打字即可，输入 /quit 退出。
echo 日常启动不联网下载模型。若要一次性补齐 SenseVoice/向量模型：run.bat --download-models
python main.py %*
if errorlevel 1 pause
