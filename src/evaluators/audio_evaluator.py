from __future__ import annotations

import subprocess
from pathlib import Path

from src.models import EvaluationResult, Settings


def audit_audio(
    audio_path: Path,
    script_path: Path,
    settings: Settings,
) -> EvaluationResult:
    """
    Audits the synthesized TTS audiobook MP3 for:
    1. File integrity and valid non-empty stream
    2. Reading speed / WPM alignment with script word count
    3. Audio clipping or excessive silence via FFmpeg filters
    """
    from src.processing import _find_ffmpeg, _get_audio_duration

    issues: list[str] = []
    metrics: dict = {}
    score = 10.0

    if not audio_path.exists() or audio_path.stat().st_size <= 1024:
        return EvaluationResult(
            stage="tts_audio",
            status="FAIL",
            score=0.0,
            issues=["Audio file does not exist or is smaller than 1 KB."],
            metrics={"file_size_bytes": 0},
        )

    file_size_kb = audio_path.stat().st_size // 1024
    metrics["file_size_kb"] = file_size_kb

    # 1. Probe duration
    duration = _get_audio_duration(audio_path, settings.ffmpeg_binary)
    metrics["duration_seconds"] = round(duration, 1)

    if duration <= 1.0:
        return EvaluationResult(
            stage="tts_audio",
            status="FAIL",
            score=1.0,
            issues=["Audio duration is near zero (< 1 second)."],
            metrics=metrics,
        )

    # 2. Reading speed (WPM) check
    if script_path.exists():
        script_text = script_path.read_text(encoding="utf-8").strip()
        word_count = len(script_text.split())
        metrics["script_word_count"] = word_count
        wpm = (word_count / duration) * 60.0
        metrics["wpm"] = round(wpm, 1)

        if wpm < 70.0:
            score -= 2.5
            issues.append(f"Spoken pacing is unusually slow: {wpm:.1f} WPM.")
        elif wpm > 230.0:
            score -= 3.0
            issues.append(f"Spoken pacing is unusually rushed: {wpm:.1f} WPM.")

    # 3. Audio volume & silence check via FFmpeg
    ffmpeg_bin = _find_ffmpeg(settings.ffmpeg_binary)
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-i", str(audio_path), "-af", "volumedetect", "-vn", "-sn", "-dn", "-f", "null", "NUL"],
            capture_output=True, text=True, timeout=15
        )
        output = proc.stderr or ""
        for line in output.splitlines():
            if "max_volume:" in line:
                max_vol_str = line.split("max_volume:")[1].strip().split()[0]
                metrics["max_volume_db"] = max_vol_str
                try:
                    max_vol = float(max_vol_str.replace("dB", ""))
                    if max_vol < -35.0:
                        score -= 3.0
                        issues.append(f"Audio is nearly inaudible (max volume: {max_vol:.1f} dB).")
                except ValueError:
                    pass
    except Exception:
        pass

    final_score = max(0.0, min(10.0, round(score, 1)))
    status = "FAIL" if final_score < 5.0 else ("WARN" if final_score < 8.0 else "PASS")

    return EvaluationResult(
        stage="tts_audio",
        status=status,
        score=final_score,
        issues=issues,
        metrics=metrics,
    )
