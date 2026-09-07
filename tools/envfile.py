"""Write capability secrets into `.env` without logging values."""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,47}$")
PROTECTED_KEYS = frozenset({"DEEPSEEK_API_KEY"})


def validate_env_key(key: str) -> str:
    name = (key or "").strip().upper()
    if not ENV_KEY_RE.fullmatch(name):
        raise ValueError("环境变量名只能是大写字母、数字和下划线，例如 QWEATHER_API_KEY。")
    if name in PROTECTED_KEYS:
        raise ValueError(f"{name} 请你自己改 .env，不能通过对话覆盖。")
    return name


def upsert_env_value(path: Path, key: str, value: str) -> None:
    """Replace or append KEY=value. Does not print the secret."""
    name = validate_env_key(key)
    secret = (value or "").strip()
    if not secret:
        raise ValueError("密钥是空的。")
    if any(ch in secret for ch in "\n\r"):
        raise ValueError("密钥不能包含换行。")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = raw.splitlines()
    prefix = f"{name}="
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) or stripped.startswith(f"#{prefix}"):
            if not replaced:
                out.append(f"{name}={secret}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append("# 能力密钥（确认后写入，不要提交 git）")
        out.append(f"{name}={secret}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[name] = secret
