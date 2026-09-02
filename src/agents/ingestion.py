from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.agents.base import AgentContext, BaseAgent
from src.downloader import download_audio
from src.processing import ProcessingError, _find_ffmpeg, _get_audio_duration


class IngestionAgent(BaseAgent):
    """
    Ingestion Agent: Handles media discovery, downloading, audio stream normalization,
    and 16kHz standard format conversion.
    """

    def __init__(self) -> None:
        super().__init__("IngestionAgent")

    def run(self, context: AgentContext) -> dict[str, Any]:
        self.log(context, f"Acquiring source audio for: {context.video.title} ({context.video.video_id})...")
        
        # 1. Download or fetch local source audio
        audio_path = download_audio(context.video, context.working_dir, context.settings.ytdlp_binary)
        if not audio_path.exists():
            raise ProcessingError(f"Failed to acquire audio file for {context.video.video_id}")

        duration = _get_audio_duration(audio_path, context.settings.ffmpeg_binary)
        if duration <= 0.0:
            duration = float(context.video.duration_seconds or 0)
        self.log(context, f"Source stream verified: {audio_path.name} (Duration: {duration:.1f}s)")

        # 2. Extract Thumbnail & Chapters for Audiobook Packaging
        thumbnail_path = context.working_dir / "thumbnail.jpg"
        if not thumbnail_path.exists():
            thumb_url = f"https://i.ytimg.com/vi/{context.video.video_id}/maxresdefault.jpg"
            try:
                import urllib.request
                urllib.request.urlretrieve(thumb_url, str(thumbnail_path))
            except Exception:
                try:
                    fallback_url = f"https://i.ytimg.com/vi/{context.video.video_id}/hqdefault.jpg"
                    urllib.request.urlretrieve(fallback_url, str(thumbnail_path))
                except Exception:
                    pass

        chapters = context.video.raw.get("chapters", [])
        if chapters:
            self.log(context, f"Extracted {len(chapters)} chapter markers from source video.")

        # 3. Audio Preprocessing (Loudness Normalization + Silence Trimming to 16kHz WAV)
        ffmpeg_bin = _find_ffmpeg(context.settings.ffmpeg_binary)
        wav_16k_path = context.working_dir / f"{context.video.video_id}_16k.wav"

        if wav_16k_path.exists() and wav_16k_path.stat().st_size > 1024:
            self.log(context, f"Reusing verified 16kHz audio on disk ({wav_16k_path.stat().st_size // 1024} KB).")
        else:
            self.log(context, "Preprocessing audio (loudnorm + silence reduction) for AMD GPU...")
            cmd = [
                ffmpeg_bin, "-y", "-i", str(audio_path),
                "-af", "loudnorm=I=-16:LRA=11:TP=-1.5:linear=true, silenceremove=stop_periods=-1:stop_duration=1:stop_threshold=-40dB",
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-vn", str(wav_16k_path)
            ]
            try:
                subprocess.run(cmd, capture_output=True, check=True, timeout=600)
            except Exception as exc:
                self.log(context, f"Warning: Advanced preprocessing failed ({exc}), falling back to direct 16k conversion.")
                subprocess.run([
                    ffmpeg_bin, "-y", "-i", str(audio_path),
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-vn", str(wav_16k_path)
                ], capture_output=True, check=True)

        return {
            "source_audio_path": audio_path,
            "wav_16k_path": wav_16k_path,
            "thumbnail_path": thumbnail_path if thumbnail_path.exists() else None,
            "chapters": chapters,
            "duration_seconds": duration,
        }
