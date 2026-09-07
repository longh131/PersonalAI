"""Personal library path aliases and default document writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from tools.builtin import BuiltinTools
from tools.library import (
    LIBRARY_SUBDIRS,
    default_browse_subdir,
    ensure_library,
    library_subdir_for,
    user_desktop,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        sqlite_path=str(tmp_path / "x.db"),
        workspace_root=str(tmp_path / "ws"),
        pai_lib_root=str(tmp_path / "PaiLib"),
        deepseek_api_key="x",
    )


def test_ensure_library_creates_subdirs(tmp_path: Path) -> None:
    root = ensure_library(tmp_path / "PaiLib")
    for sub in LIBRARY_SUBDIRS:
        assert (root / sub).is_dir()
    assert (root / "说明.txt").is_file()


@pytest.mark.asyncio
async def test_write_defaults_to_library_files(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    tools = BuiltinTools(_settings(tmp_path))
    result = await tools.write_file("备忘.txt", "hello")
    target = tmp_path / "PaiLib" / "files" / "notes" / "备忘.txt"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "hello"
    assert "已写入" in result
    assert str(ws / "备忘.txt") not in result


@pytest.mark.asyncio
async def test_write_desktop_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr("tools.builtin.user_desktop", lambda: desktop)
    tools = BuiltinTools(_settings(tmp_path))
    tools.desktop_dir = desktop
    result = await tools.write_file("桌面/便签.txt", "desk")
    target = desktop / "便签.txt"
    assert target.read_text(encoding="utf-8") == "desk"
    assert "已写入" in result


@pytest.mark.asyncio
async def test_read_prefers_workspace_then_library(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("PersonalAIEngine", encoding="utf-8")
    tools = BuiltinTools(_settings(tmp_path))
    assert "PersonalAIEngine" in await tools.read_file("main.py")
    await tools.write_file("笔记.txt", "lib-note")
    assert "lib-note" in await tools.read_file("笔记.txt")


@pytest.mark.asyncio
async def test_search_covers_workspace_and_library(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("x", encoding="utf-8")
    tools = BuiltinTools(_settings(tmp_path))
    await tools.write_file("笔记.txt", "n")
    listed = await tools.search_files("main.py")
    assert "main.py" in listed
    notes = await tools.search_files("**/*.txt", root="资料库")
    assert "笔记.txt" in notes


@pytest.mark.asyncio
async def test_write_routes_by_extension(tmp_path: Path) -> None:
    tools = BuiltinTools(_settings(tmp_path))
    await tools.write_file("备忘.txt", "n")
    await tools.write_file("合同.pdf", "%PDF")
    await tools.write_file("未命名", "x")
    lib = tmp_path / "PaiLib"
    assert (lib / "files" / "notes" / "备忘.txt").is_file()
    assert (lib / "files" / "pdf" / "合同.pdf").is_file()
    assert (lib / "files" / "inbox" / "未命名").is_file()
    listed = await tools.search_files("*.txt")
    assert "备忘.txt" in listed
    pdfs = await tools.search_files("*.pdf")
    assert "合同.pdf" in pdfs
    inbox = await tools.search_files("*")
    assert "未命名" in inbox


def test_classify_helpers() -> None:
    assert library_subdir_for("a.txt") == "files/notes"
    assert library_subdir_for("a.PDF") == "files/pdf"
    assert library_subdir_for("a.docx") == "files/word"
    assert library_subdir_for("a.xlsx") == "files/sheets"
    assert library_subdir_for("a.pptx") == "files/slides"
    assert library_subdir_for("a.mp3") == "music"
    assert library_subdir_for("a.jpg") == "pictures"
    assert library_subdir_for("a.mp4") == "videos"
    assert library_subdir_for("a") == "files/inbox"
    assert default_browse_subdir("*.pdf") == "files/pdf"
    assert default_browse_subdir("*") == "files/inbox"
    assert default_browse_subdir("main.py") == ""


@pytest.mark.asyncio
async def test_delete_outside_allowed_roots_denied(tmp_path: Path) -> None:
    tools = BuiltinTools(_settings(tmp_path))
    outside = tmp_path / "other.txt"
    outside.write_text("no", encoding="utf-8")
    tools.last_user_text = "确认删除"
    result = await tools.delete_file(str(outside), confirm=True)
    assert "只能删除" in result
    assert outside.exists()


def test_user_desktop_exists() -> None:
    assert user_desktop().name.lower() in {"desktop", "桌面"}
