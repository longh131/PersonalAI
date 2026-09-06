"""Runtime settings loaded from environment variables and `.env`."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Process-wide configuration for Personal AI OS.

    Attributes are populated from environment variables (see `.env.template`).
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_provider: str = "deepseek"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    sqlite_path: str = "data/personal_ai.db"
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    log_level: str = "INFO"
    max_tool_iterations: int = 8
    short_term_window: int = 16

    voice_enabled: bool = True
    tts_enabled: bool = True
    stt_model: str = "base"
    stt_engine: str = "sensevoice"
    sensevoice_dir: str = "models/sensevoice"
    tts_engine: str = "edge"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    vision_enabled: bool = True
    workspace_root: str = Field(default=".")
    mcp_server_url: str = ""

    # Voice session (seconds / energy). Tune in .env without code changes.
    voice_silence_end: float = 1.0
    voice_min_utterance: float = 0.4
    voice_max_utterance: float = 15.0
    voice_followup_seconds: float = 10.0
    voice_start_timeout: float = 8.0
    voice_start_energy: float = 0.0025
    voice_keep_energy: float = 0.0012
    voice_start_ratio: float = 8.0
    voice_keep_ratio: float = 4.0
    voice_hotkey: str = "CTRL+SHIFT+L"
    voice_barge_in_grace: float = 0.8
    voice_barge_in_energy: float = 0.035
    voice_barge_in_ratio: float = 3.0
    wakeword_enabled: bool = True
    tray_enabled: bool = True
    wake_max_utterance: float = 4.0
    web_search_backend: str = "bing"
    web_search_timeout: float = 8.0
    web_search_proxy: str = ""

    def resolve_sqlite_path(self) -> Path:
        """Return an absolute SQLite path, creating the parent directory."""
        path = Path(self.sqlite_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_workspace(self) -> Path:
        """Return the workspace root used by file tools."""
        path = Path(self.workspace_root)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    def identity_path(self) -> Path:
        """Return the YAML identity configuration path."""
        return PROJECT_ROOT / "config" / "identity.yaml"

    def logs_dir(self) -> Path:
        """Return the log directory, creating it if needed."""
        path = PROJECT_ROOT / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path


def load_settings() -> Settings:
    """Load settings from `.env` and the process environment.

    Returns:
        A validated `Settings` instance.
    """
    return Settings()


def verify_module() -> None:
    """Smoke-check that settings can be constructed without a live `.env`."""
    settings = Settings()
    assert settings.deepseek_base_url
    assert settings.max_tool_iterations >= 1
