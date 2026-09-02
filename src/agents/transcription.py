from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.agents.base import AgentContext, BaseAgent
from src.evaluators import audit_transcript
from src.processing import ProcessingError, _get_ggml_model, _is_transcript_complete
from src.text_cleaner import clean_transcript_text, deduplicate_segments, format_paragraphs


class TranscriptionAgent(BaseAgent):
    """
    Acoustic Perception Agent: Runs high-precision Vulkan AMD GPU Whisper transcription
    with anti-hallucination guardrails and quality auditing.
    """

    def __init__(self) -> None:
        super().__init__("AcousticAgent")

    def run(self, context: AgentContext) -> dict[str, Any]:
        transcript_path = context.working_dir / "transcript.json"
        txt_path = context.working_dir / "transcript.txt"
        wav_path = context.state.get("wav_16k_path") or (context.working_dir / f"{context.video.video_id}_16k.wav")
        audio_dur = context.state.get("duration_seconds", 0.0)

        # 1. Check verified cached transcript
        if transcript_path.exists():
            try:
                cached = json.loads(transcript_path.read_text(encoding="utf-8"))
                if _is_transcript_complete(cached, wav_path, context.settings):
                    self.log(context, f"Verified complete transcript found on disk ({len(cached.get('segments', []))} segments).")
                    audit_res = audit_transcript(cached, audio_dur)
                    return {
                        "transcript_path": transcript_path,
                        "txt_path": txt_path,
                        "transcript_data": cached,
                        "audit_result": audit_res,
                    }
                else:
                    self.log(context, "Cached transcript is incomplete. Re-transcribing from scratch...")
                    transcript_path.unlink()
            except Exception:
                pass

        # 2. Run AMD Vulkan Whisper Engine
        exe = context.settings.root / "tools" / "whisper_vulkan" / "whisper-cli.exe"
        if not exe.exists() or sys.platform != "win32":
            raise ProcessingError("AMD Vulkan Whisper engine binary not found in tools/whisper_vulkan/")

        model_path = _get_ggml_model(context.settings, context.settings.whisper_model)
        threads = str(min(os.cpu_count() or 4, 8))
        out_json_base = context.working_dir / f"{context.video.video_id}_vulkan_out"
        out_json_file = context.working_dir / f"{context.video.video_id}_vulkan_out.json"

        self.log(context, f"Transcribing on AMD Radeon RX 6600 GPU ({context.settings.whisper_model}, {threads} threads, Flash Attention)...")
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
            raise ProcessingError(f"Vulkan Whisper execution failed with exit code {proc.returncode}")

        if not out_json_file.exists():
            raise ProcessingError(f"Vulkan Whisper completed but output file not found: {out_json_file}")

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

        # Clean temporary vulkan raw json
        try:
            out_json_file.unlink()
        except Exception:
            pass

        return {
            "transcript_path": transcript_path,
            "txt_path": txt_path,
            "transcript_data": payload,
            "audit_result": audit_res,
        }
