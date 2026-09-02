from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from src.agents.base import AgentContext, BaseAgent
from src.evaluators import audit_audio
from src.processing import ProcessingError, _find_ffmpeg, synthesize


class AudiobookAgent(BaseAgent):
    """
    Audiobook Director Agent: Generates neural spoken audiobooks and applies
    professional audio mastering (EBU R128 dialogue leveling & silence trimming).
    """

    def __init__(self) -> None:
        super().__init__("AudiobookAgent")

    def run(self, context: AgentContext) -> dict[str, Any]:
        output_audio = context.working_dir / "narration.mp3"
        script_path = context.working_dir / "summary.txt"
        target_language = context.state.get("target_language", "English")

        if context.settings.tts_provider.lower().strip() in ("none", "disabled", "false", "0"):
            self.log(context, "TTS synthesis is disabled in settings.")
            return {"audio_path": None, "audio_guard_score": 0.0}

        # 1. Synthesize neural speech
        self.log(context, f"Directing Neural Voice Synthesis ({target_language})...")
        rendered = synthesize(context.settings, script_path, output_audio, target_language)

        if not rendered or not output_audio.exists() or output_audio.stat().st_size <= 1024:
            raise ProcessingError("Audiobook synthesis failed to produce a valid audio file.")

        # 2. Master Audio (EBU R128 Loudness Normalization + Dynamic Range Polish)
        mastered_audio = context.working_dir / "narration_mastered.mp3"
        ffmpeg_bin = _find_ffmpeg(context.settings.ffmpeg_binary)

        self.log(context, "Mastering audiobook with EBU R128 loudness normalization (linear mode)...")
        try:
            cmd = [
                ffmpeg_bin, "-y", "-i", str(output_audio),
                "-af", "loudnorm=I=-16:LRA=11:TP=-1.5:linear=true, silenceremove=stop_periods=-1:stop_duration=1.5:stop_threshold=-45dB",
                "-c:a", "libmp3lame", "-b:a", "192k", str(mastered_audio)
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=120)
            if mastered_audio.exists() and mastered_audio.stat().st_size > 1024:
                # Replace output_audio with mastered version
                mastered_audio.replace(output_audio)
                self.log(context, "Audiobook mastered successfully (192kbps MP3).")
        except Exception as exc:
            self.log(context, f"Notice: Mastering filter skipped ({exc}), using clean direct audio.")

        # 3. Embed ID3v2 Tags, Thumbnail Cover Art & Chapter Markers
        from src.metadata_embedder import embed_audiobook_metadata
        thumb_path = context.state.get("thumbnail_path")
        chapters = context.state.get("chapters", [])
        
        self.log(context, f"Embedding cover art, ID3v2 tags (Artist: {context.settings.podcast_author}), and chapters...")
        embed_audiobook_metadata(
            audio_file=output_audio,
            title=context.video.title,
            artist=context.settings.podcast_author or context.video.channel_title or "Azhar",
            album=context.settings.podcast_playlist_name or "Azhar's AI Audiobooks",
            thumbnail_file=thumb_path,
            chapters=chapters,
        )

        # 4. Audio Quality Guard Audit
        audio_audit = audit_audio(output_audio, script_path, context.settings)
        self.log(context, f"Audio Guard Verdict: {audio_audit.status} [Score: {audio_audit.score}/10] (Pacing: {audio_audit.metrics.get('wpm', 0)} WPM, Size: {audio_audit.metrics.get('file_size_kb', 0)} KB)")

        return {
            "audio_path": output_audio,
            "audio_guard_score": audio_audit.score,
            "audit_result": audio_audit,
        }
