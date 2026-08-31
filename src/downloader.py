from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .models import Video


class DownloadError(RuntimeError):
    pass


def _get_ytdlp_cmd(ytdlp_binary: str = "yt-dlp") -> list[str]:
    """Find the best way to invoke yt-dlp (system PATH, venv Scripts, or python -m yt_dlp)."""
    if shutil.which(ytdlp_binary) or Path(ytdlp_binary).is_file():
        return [ytdlp_binary]
    # Check current Python interpreter directory (e.g. .venv/Scripts/yt-dlp.exe)
    venv_exe = Path(sys.executable).parent / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")
    if venv_exe.exists():
        return [str(venv_exe)]
    # Fallback to python -m yt_dlp
    return [sys.executable, "-m", "yt_dlp"]


def get_videos_from_url(url: str, ytdlp_binary: str = "yt-dlp") -> list[Video]:
    """Extract all video information from a playlist or single video URL using yt-dlp."""
    cmd = _get_ytdlp_cmd(ytdlp_binary) + ["--flat-playlist", "--dump-single-json", "--no-warnings", url]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except FileNotFoundError as exc:
        raise DownloadError(f"yt-dlp executable was not found: {ytdlp_binary}") from exc
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or "").strip()
        raise DownloadError(f"yt-dlp failed to fetch playlist/video metadata: {msg[-800:]}") from exc

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DownloadError("Failed to parse yt-dlp JSON output") from exc

    videos: list[Video] = []
    # If it's a playlist or channel
    if "entries" in data and data["entries"]:
        for entry in data["entries"]:
            if not entry:
                continue
            vid_id = entry.get("id") or entry.get("url")
            if not vid_id:
                continue
            title = entry.get("title", f"Video {vid_id}")
            channel = entry.get("uploader", "") or entry.get("channel", "")
            duration = int(entry.get("duration") or 0)
            full_url = f"https://www.youtube.com/watch?v={vid_id}" if not entry.get("url", "").startswith("http") else entry["url"]
            videos.append(Video(
                video_id=vid_id,
                title=title,
                url=full_url,
                channel_title=channel,
                duration_seconds=duration,
                raw=entry,
            ))
    else:
        # Single video
        vid_id = data.get("id") or url
        title = data.get("title", f"Video {vid_id}")
        channel = data.get("uploader", "") or data.get("channel", "")
        duration = int(data.get("duration") or 0)
        full_url = data.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid_id}" if not url.startswith("http") else url)
        videos.append(Video(
            video_id=vid_id,
            title=title,
            url=full_url,
            channel_title=channel,
            duration_seconds=duration,
            raw=data,
        ))

    return videos


def get_local_media_videos(input_dir: Path) -> list[Video]:
    """Scan a local directory for audio/video files (.mp3, .wav, .m4a, .mp4, .mkv, .webm, .flac)."""
    if not input_dir.exists():
        return []
    valid_exts = {".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".webm", ".flac", ".aac", ".ogg"}
    media_files = [f for f in input_dir.iterdir() if f.is_file() and f.suffix.lower() in valid_exts]
    
    videos: list[Video] = []
    for f in sorted(media_files):
        # Sanitize video_id for folder names
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in f.stem)
        vid_id = f"local_{safe_id}"
        videos.append(Video(
            video_id=vid_id,
            title=f.stem,
            url=str(f.resolve()),
            channel_title="Local Media",
            duration_seconds=0,
            raw={"path": str(f.resolve()), "type": "local"},
        ))
    return videos


def download_audio(video: Video, output_dir: Path, ytdlp_binary: str = "yt-dlp") -> Path:
    """Download the audio stream for a given video, or copy local media file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = next(output_dir.glob("source_audio.*"), None)
    if existing and existing.stat().st_size > 0:
        return existing

    # Handle local media file
    if video.raw.get("type") == "local" or (Path(video.url).is_file() and not video.url.startswith("http")):
        src_file = Path(video.url)
        dest_file = output_dir / f"source_audio{src_file.suffix}"
        if not dest_file.exists():
            shutil.copy2(src_file, dest_file)
        return dest_file

    output_template = str(output_dir / "source_audio.%(ext)s")
    cmd = _get_ytdlp_cmd(ytdlp_binary) + [
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "m4a",
        "--output", output_template,
        video.url,
    ]
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or "").strip()
        raise DownloadError(f"Audio download failed for {video.video_id}: {msg[-800:]}") from exc

    created = next(output_dir.glob("source_audio.*"), None)
    if not created or created.stat().st_size == 0:
        raise DownloadError(f"yt-dlp completed but did not produce audio file for {video.video_id}")
    return created
