"""Personal library root. Configured via PAI_LIB_ROOT. Open files with Windows defaults."""

from __future__ import annotations

from pathlib import Path

LIBRARY_SUBDIRS = (
    "music",
    "pictures",
    "pictures/inbox",
    "videos",
    "files",
    "files/inbox",
    "files/notes",
    "files/pdf",
    "files/word",
    "files/sheets",
    "files/slides",
)

CODE_EXTENSIONS = {
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".bat",
    ".ps1",
    ".env",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
}

_EXTENSION_SUBDIR = {
    ".txt": "files/notes",
    ".md": "files/notes",
    ".rst": "files/notes",
    ".pdf": "files/pdf",
    ".doc": "files/word",
    ".docx": "files/word",
    ".wps": "files/word",
    ".xls": "files/sheets",
    ".xlsx": "files/sheets",
    ".csv": "files/sheets",
    ".ppt": "files/slides",
    ".pptx": "files/slides",
    ".mp3": "music",
    ".wav": "music",
    ".flac": "music",
    ".m4a": "music",
    ".aac": "music",
    ".ogg": "music",
    ".wma": "music",
    ".jpg": "pictures",
    ".jpeg": "pictures",
    ".png": "pictures",
    ".gif": "pictures",
    ".webp": "pictures",
    ".bmp": "pictures",
    ".heic": "pictures",
    ".mp4": "videos",
    ".mkv": "videos",
    ".avi": "videos",
    ".mov": "videos",
    ".wmv": "videos",
    ".webm": "videos",
}

_LIBRARY_GUIDE = """小派资料库

换电脑时，在项目 .env 里把 PAI_LIB_ROOT 改成这个文件夹的路径即可。
打开文件一律用 Windows 默认程序（和资源管理器里双击相同）。

music\\              音乐
pictures\\           图片
pictures\\inbox\\    预留拍照 / 入库
videos\\             电影 / 录像
files\\inbox\\       未分类。没说类型、也没有扩展名时，默认这里
files\\notes\\       备忘 txt / md
files\\pdf\\         PDF
files\\word\\        Word
files\\sheets\\      表格
files\\slides\\      PPT

明确说「桌面」仍会写到桌面。说「工作区」才进项目代码目录。
"""


def user_desktop() -> Path:
    """Windows Desktop, preferring OneDrive Desktop when it exists."""
    home = Path.home()
    onedrive = home / "OneDrive" / "Desktop"
    if onedrive.is_dir():
        return onedrive.resolve()
    return (home / "Desktop").resolve()


def pattern_suffix(pattern: str) -> str:
    """Best-effort file suffix from a name or glob (*.pdf, **/*.txt, 合同.docx)."""
    name = (pattern or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return Path(name).suffix.lower()


def is_code_name(name: str) -> bool:
    return pattern_suffix(name) in CODE_EXTENSIONS


def library_subdir_for(name: str) -> str:
    """Relative folder under PaiLib for a file name when the user did not pick a directory."""
    suffix = pattern_suffix(name)
    if not suffix:
        return "files/inbox"
    return _EXTENSION_SUBDIR.get(suffix, "files/inbox")


def default_browse_subdir(pattern: str = "") -> str:
    """Folder to list or open when browsing without an explicit directory."""
    suffix = pattern_suffix(pattern)
    if not suffix or pattern.strip() in {"", "*", "**", "**/*"}:
        return "files/inbox"
    if suffix in CODE_EXTENSIONS:
        return ""
    return library_subdir_for(pattern)


def ensure_library(root: Path) -> Path:
    """Create the library root and standard subfolders."""
    root.mkdir(parents=True, exist_ok=True)
    for sub in LIBRARY_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "说明.txt").write_text(_LIBRARY_GUIDE, encoding="utf-8")
    return root.resolve()
