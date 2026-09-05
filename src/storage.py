from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from src.models import QualityScorecard, Settings

logger = logging.getLogger("StorageManager")


class StorageManager:
    """
    Centralized manager for artifact persistence, cache validation, and media exports.
    Adheres to Single Responsibility Principle by decoupling storage mechanics from agent logic.
    """

    @staticmethod
    def is_transcription_cached(transcript_path: Path, audio_path: Path, settings: Settings) -> dict[str, Any] | None:
        """Verifies if a complete, uncorrupted transcript exists on disk."""
        if not transcript_path.exists():
            return None

        try:
            cached = json.loads(transcript_path.read_text(encoding="utf-8"))
            from src.processing import _is_transcript_complete
            if _is_transcript_complete(cached, audio_path, settings):
                logger.debug(f"Cache HIT for transcript: {transcript_path}")
                return cached
            else:
                logger.info(f"Existing transcript at {transcript_path} is incomplete. Invalidation required.")
                StorageManager.safe_delete(transcript_path)
        except Exception as exc:
            logger.warning(f"Corrupted transcript cache at {transcript_path}: {exc}. Re-transcribing.")
            StorageManager.safe_delete(transcript_path)

        return None

    @staticmethod
    def is_narration_cached(narration_path: Path, expected_chunk_count: int) -> dict[str, Any] | None:
        """Verifies if a complete narration JSON summary is cached on disk."""
        if not narration_path.exists():
            return None

        try:
            cached = json.loads(narration_path.read_text(encoding="utf-8"))
            from src.processing import _is_narration_complete
            if _is_narration_complete(cached, expected_chunk_count):
                logger.debug(f"Cache HIT for narration: {narration_path}")
                return cached
            else:
                logger.info(f"Existing narration at {narration_path} is incomplete. Re-synthesizing.")
                StorageManager.safe_delete(narration_path)
        except Exception as exc:
            logger.warning(f"Corrupted narration cache at {narration_path}: {exc}.")
            StorageManager.safe_delete(narration_path)

        return None

    @staticmethod
    def is_audiobook_cached(audio_path: Path) -> Path | None:
        """Verifies if a valid mastered audiobook exists on disk."""
        if audio_path.exists() and audio_path.stat().st_size > 1024:
            logger.debug(f"Cache HIT for audiobook: {audio_path}")
            return audio_path
        return None

    @staticmethod
    def safe_delete(path: Path) -> bool:
        """Safely removes a file with proper logging instead of silent failure."""
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except Exception as exc:
            logger.debug(f"Could not delete file {path}: {exc}")
            return False

    @staticmethod
    def export_finished_media(
        settings: Settings,
        video_id: str,
        title: str,
        audio_src: Path | None,
        script: str,
        transcript_txt_src: Path | None,
        scorecard: QualityScorecard,
        working_dir: Path,
    ) -> dict[str, str]:
        """
        Exports mastered audio, summary markdown, full transcript, and QA report
        into organized subdirectories under data/output/.
        """
        output_dir = settings.data_dir / "output"
        books_dir = output_dir / "audiobooks"
        summaries_dir = output_dir / "summaries"
        transcripts_dir = output_dir / "transcripts"
        reports_dir = output_dir / "reports"

        for directory in (books_dir, summaries_dir, transcripts_dir, reports_dir):
            directory.mkdir(parents=True, exist_ok=True)

        safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip()
        if not safe_title:
            safe_title = video_id

        # 1. Export Audio
        exported_audio = ""
        if audio_src and audio_src.exists() and audio_src.stat().st_size > 1024:
            dest_audio = books_dir / f"{safe_title}.mp3"
            shutil.copy2(str(audio_src), str(dest_audio))
            exported_audio = str(dest_audio)

        # 2. Export Summary Markdown
        dest_summary = summaries_dir / f"{safe_title}.md"
        dest_summary.write_text(script, encoding="utf-8")

        # 3. Export Transcript Text
        dest_trans = ""
        if transcript_txt_src and transcript_txt_src.exists():
            dest_trans_path = transcripts_dir / f"{safe_title}.txt"
            shutil.copy2(str(transcript_txt_src), str(dest_trans_path))
            dest_trans = str(dest_trans_path)

        # 4. Export Quality Scorecard
        scorecard_dict = scorecard.to_dict()
        report_file = reports_dir / f"{video_id}_quality.json"
        report_file.write_text(json.dumps(scorecard_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        (working_dir / "quality_report.json").write_text(
            json.dumps(scorecard_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return {
            "audio_path": exported_audio,
            "summary_path": str(dest_summary),
            "transcript_path": dest_trans,
            "report_path": str(report_file),
        }

    @staticmethod
    def clean_intermediate_artifacts(working_dir: Path) -> list[str]:
        """
        Cleans bulky intermediate files (*_16k.wav, source_audio.*) in the working directory
        to prevent disk exhaustion after final media export. Preserves json manifests and text summaries.
        """
        removed: list[str] = []
        if not working_dir.exists():
            return removed

        # Clean uncompressed 16kHz WAV files (~115 MB/hour)
        for wav_file in working_dir.glob("*_16k.wav"):
            if StorageManager.safe_delete(wav_file):
                removed.append(wav_file.name)

        # Clean downloaded source audio within the working directory
        for src_audio in working_dir.glob("source_audio.*"):
            if StorageManager.safe_delete(src_audio):
                removed.append(src_audio.name)

        return removed
