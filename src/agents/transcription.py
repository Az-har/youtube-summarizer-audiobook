from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.agents.base import AgentContext, BaseAgent
from src.evaluators import audit_transcript
from src.models import IngestionResult, TranscriptionResult
from src.processing import ProcessingError, _get_ggml_model
from src.storage import StorageManager
from src.text_cleaner import clean_transcript_text, deduplicate_segments, format_paragraphs

logger = logging.getLogger("TranscriptionAgent")


def _find_whisper_cli(root: Path) -> Path | None:
    """Discovers whisper-cli binary across Windows, Linux, and macOS platforms."""
    binary_name = "whisper-cli.exe" if sys.platform == "win32" else "whisper-cli"

    candidates = [
        root / "tools" / "whisper_vulkan" / binary_name,
        root / "tools" / binary_name,
        root / "tools" / "whisper_vulkan" / "whisper-cli",
    ]

    for cand in candidates:
        if cand.exists() and cand.is_file():
            return cand

    which_path = shutil.which("whisper-cli") or shutil.which("whisper")
    if which_path:
        return Path(which_path)

    return None


class TranscriptionAgent(BaseAgent):
    """
    Acoustic Perception Agent: Runs high-precision Vulkan/GPU Whisper transcription
    with cross-platform discovery, anti-hallucination guardrails, and quality auditing.
    """

    def __init__(self) -> None:
        super().__init__("AcousticAgent")

    def run(self, context: AgentContext, ingestion: IngestionResult | None = None) -> TranscriptionResult:
        transcript_path = context.working_dir / "transcript.json"
        txt_path = context.working_dir / "transcript.txt"

        # Resolve wav path & duration from typed DTO, context, or state
        if ingestion:
            wav_path = ingestion.wav_16k_path or (context.working_dir / f"{context.video.video_id}_16k.wav")
            audio_dur = ingestion.duration_seconds
        elif context.ingestion_result:
            wav_path = context.ingestion_result.wav_16k_path or (context.working_dir / f"{context.video.video_id}_16k.wav")
            audio_dur = context.ingestion_result.duration_seconds
        else:
            wav_path = context.state.get("wav_16k_path") or (context.working_dir / f"{context.video.video_id}_16k.wav")
            audio_dur = float(context.state.get("duration_seconds", 0.0))

        # 1. Check verified cached transcript via StorageManager
        cached = StorageManager.is_transcription_cached(transcript_path, wav_path, context.settings)
        if cached:
            self.log(context, f"Verified complete transcript found on disk ({len(cached.get('segments', []))} segments).")
            audit_res = audit_transcript(cached, audio_dur)
            result = TranscriptionResult(
                transcript_path=transcript_path,
                txt_path=txt_path,
                transcript_data=cached,
                audit_result=audit_res,
            )
            context.transcription_result = result
            return result

        # 2. Locate Whisper CLI Engine (Cross-platform)
        exe = _find_whisper_cli(context.settings.root)
        if not exe:
            # Fallback to faster-whisper (cross-platform CPU/GPU)
            self.log(context, "Native whisper-cli binary not found; falling back to faster-whisper engine...")
            return self._run_faster_whisper_fallback(context, wav_path, transcript_path, txt_path, audio_dur)

        model_path = _get_ggml_model(context.settings, context.settings.whisper_model)
        threads = str(min(os.cpu_count() or 4, 8))
        out_json_base = context.working_dir / f"{context.video.video_id}_vulkan_out"
        out_json_file = context.working_dir / f"{context.video.video_id}_vulkan_out.json"

        self.log(context, f"Transcribing with Whisper Engine ({context.settings.whisper_model}, {threads} threads, Flash Attention)...")
        print("  " + "=" * 55)

        cmd = [
            str(exe),
            "-m", str(model_path),
            "-f", str(wav_path),
            "-t", threads,
            "-mc", "0",          # Anti-looping: disable cross-chunk prompt conditioning
            "-sns",              # Suppress non-speech hallucination tokens
            "-nth", "0.60",      # No-speech probability threshold
            "-et", "2.40",       # Repetition entropy cutoff
            "-lpt", "-1.00",     # Logprob cutoff
            "-bo", "3",          # Candidate search pool
            "-bs", "3",          # Beam size
            "-oj",
            "-of", str(out_json_base),
            "--flash-attn"
        ]

        proc = subprocess.run(cmd)
        print("  " + "=" * 55)
        if proc.returncode != 0:
            raise ProcessingError(f"Whisper execution failed with exit code {proc.returncode}")

        if not out_json_file.exists():
            raise ProcessingError(f"Whisper completed but output file not found: {out_json_file}")

        # 3. Parse & Clean Segments
        data = json.loads(out_json_file.read_text(encoding="utf-8", errors="replace"))
        transcription_list = data.get("transcription", [])
        segment_data = []

        for item in transcription_list:
            raw_text = (item.get("text") or "").strip()
            text = clean_transcript_text(raw_text)
            if not text:
                continue
            offsets = item.get("offsets", {})
            start = offsets.get("from", 0) / 1000.0
            end = offsets.get("to", 0) / 1000.0
            segment_data.append({
                "start": start,
                "end": end,
                "text": text,
                "avg_logprob": 0.0,
            })

        # 4. Anti-loop segment deduplication
        segment_data = deduplicate_segments(segment_data, max_consecutive_repeats=2)

        detected_lang = data.get("result", {}).get("language", "en")
        if detected_lang == "auto" or not detected_lang:
            tamil_chars = sum(1 for s in segment_data for ch in s.get("text", "") if '\u0b80' <= ch <= '\u0bff')
            detected_lang = "ta" if tamil_chars > 20 else "en"

        payload = {
            "language": detected_lang,
            "language_probability": 0.99,
            "segments": segment_data,
        }

        transcript_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        txt_path.write_text(format_paragraphs(segment_data), encoding="utf-8")

        # 5. Acoustic Audit
        audit_res = audit_transcript(payload, audio_dur)
        self.log(context, f"Acoustic Audit: {audit_res.status} [Score: {audit_res.score}/10] ({len(segment_data)} segments, {audit_res.metrics.get('wpm', 0)} WPM)")

        StorageManager.safe_delete(out_json_file)

        result = TranscriptionResult(
            transcript_path=transcript_path,
            txt_path=txt_path,
            transcript_data=payload,
            audit_result=audit_res,
        )
        context.transcription_result = result
        return result

    def _run_faster_whisper_fallback(
        self,
        context: AgentContext,
        wav_path: Path,
        transcript_path: Path,
        txt_path: Path,
        audio_dur: float,
    ) -> TranscriptionResult:
        """Cross-platform faster-whisper fallback for Linux, macOS, and environments without whisper-cli."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ProcessingError("Neither whisper-cli nor faster-whisper is available.") from exc

        self.log(context, f"Loading faster-whisper model '{context.settings.whisper_model}'...")
        threads = min(os.cpu_count() or 4, 8)
        model = WhisperModel(
            context.settings.whisper_model,
            device="auto",
            compute_type="default",
            cpu_threads=threads,
        )

        segments, info = model.transcribe(
            str(wav_path),
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            condition_on_previous_text=False,
        )

        segment_data = []
        for s in segments:
            text = clean_transcript_text(s.text.strip())
            if text:
                segment_data.append({
                    "start": s.start,
                    "end": s.end,
                    "text": text,
                    "avg_logprob": getattr(s, "avg_logprob", 0.0),
                })

        segment_data = deduplicate_segments(segment_data, max_consecutive_repeats=2)

        payload = {
            "language": getattr(info, "language", "en"),
            "language_probability": getattr(info, "language_probability", 0.99),
            "segments": segment_data,
        }

        transcript_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        txt_path.write_text(format_paragraphs(segment_data), encoding="utf-8")

        audit_res = audit_transcript(payload, audio_dur)
        result = TranscriptionResult(
            transcript_path=transcript_path,
            txt_path=txt_path,
            transcript_data=payload,
            audit_result=audit_res,
        )
        context.transcription_result = result
        return result
