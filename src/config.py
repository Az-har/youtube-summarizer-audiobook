from __future__ import annotations

import os
from pathlib import Path

from .models import Settings


def load_env(path: Path) -> None:
    """Load a simple .env file without adding an external dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.lstrip("\ufeff").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(root: Path) -> Settings:
    load_env(root / ".env")
    env = os.environ.get
    return Settings(
        root=root,
        data_dir=root / "data",
        completed_file=root / "data" / "completed_videos.txt",
        ollama_base_url=env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=env("OLLAMA_MODEL", "qwen3:14b"),
        whisper_model=env("WHISPER_MODEL", "large-v3"),
        whisper_device=env("WHISPER_DEVICE", "auto"),
        whisper_compute_type=env("WHISPER_COMPUTE_TYPE", "int8"),
        tts_provider=env("TTS_PROVIDER", "command"),
        tts_command_template=env("TTS_COMMAND_TEMPLATE", ""),
        tts_voice_tamil=env("TTS_VOICE_TAMIL", ""),
        tts_voice_english=env("TTS_VOICE_ENGLISH", ""),
        ffmpeg_binary=env("FFMPEG_BINARY", "ffmpeg"),
        ytdlp_binary=env("YTDLP_BINARY", "yt-dlp"),
    )


def load_playlist_urls(path: Path) -> list[str]:
    """Read URLs from a text file (one URL per line, # comments ignored)."""
    if not path.exists():
        return []
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.lstrip("\ufeff").strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls

