from __future__ import annotations

from pathlib import Path
from src.models import EvaluationResult


def _calculate_ngram_repetition_rate(words: list[str] | str, n: int = 4) -> float:
    """Computes the ratio of repeated n-grams in text or token list."""
    if isinstance(words, str):
        words = words.lower().split()
    total_ngrams = len(words) - n + 1
    if total_ngrams <= 0:
        return 0.0
    unique_count = len({tuple(words[i:i + n]) for i in range(total_ngrams)})
    return 1.0 - (unique_count / total_ngrams)


def audit_transcript(transcript_data: dict, audio_duration: float = 0.0) -> EvaluationResult:
    """
    Audits a Whisper transcript for completeness, looping hallucinations,
    speech rate density, and content integrity.
    """
    issues: list[str] = []
    metrics: dict = {}
    score = 10.0

    segments = transcript_data.get("segments", [])
    if not isinstance(segments, list) or not segments:
        return EvaluationResult(
            stage="transcription",
            status="FAIL",
            score=0.0,
            issues=["Transcript contains no segments or is empty."],
            metrics={"segment_count": 0},
        )

    segment_count = len(segments)
    metrics["segment_count"] = segment_count

    # Pre-tokenize all segment words once to avoid repeated string splitting and memory thrashing
    words = [w.lower() for s in segments for w in s.get("text", "").split()]
    total_words = len(words)
    metrics["word_count"] = total_words

    # 1. Coverage Check
    last_end = float(segments[-1].get("end", 0.0))
    metrics["covered_duration_seconds"] = round(last_end, 1)
    if audio_duration > 20.0:
        coverage_ratio = min(1.0, last_end / audio_duration)
        metrics["coverage_ratio"] = round(coverage_ratio, 3)
        if coverage_ratio < 0.70:
            score -= 4.0
            issues.append(f"Incomplete audio coverage: {coverage_ratio * 100:.1f}% covered ({last_end:.1f}s / {audio_duration:.1f}s).")
        elif coverage_ratio < 0.85:
            score -= 1.5
            issues.append(f"Partial trailing audio uncaptured: {coverage_ratio * 100:.1f}% covered.")

    # 2. Speech Density (WPM) Check
    if last_end > 10.0:
        wpm = (total_words / last_end) * 60.0
        metrics["wpm"] = round(wpm, 1)
        if wpm < 40.0:
            score -= 2.5
            issues.append(f"Abnormally low speech density: {wpm:.1f} WPM (possible dropped audio segments).")
        elif wpm > 280.0:
            score -= 3.0
            issues.append(f"Abnormally high speech density: {wpm:.1f} WPM (possible text explosion / hallucination).")

    # 3. Looping Hallucination Check using pre-tokenized words
    rep_rate_4gram = _calculate_ngram_repetition_rate(words, n=4)
    rep_rate_6gram = _calculate_ngram_repetition_rate(words, n=6)
    metrics["repetition_rate_4gram"] = round(rep_rate_4gram, 3)
    metrics["repetition_rate_6gram"] = round(rep_rate_6gram, 3)

    if rep_rate_6gram > 0.25:
        score -= 4.0
        issues.append(f"High 6-gram repetition ({rep_rate_6gram * 100:.1f}%): severe Whisper looping hallucination detected.")
    elif rep_rate_4gram > 0.35:
        score -= 2.0
        issues.append(f"Elevated 4-gram repetition ({rep_rate_4gram * 100:.1f}%): repetitive phrases detected.")

    # 4. Short Segment / Silence Ratio
    empty_or_tiny = sum(1 for s in segments if len(s.get("text", "").strip()) < 3)
    if empty_or_tiny > (segment_count * 0.4) and segment_count > 10:
        score -= 1.5
        issues.append(f"{empty_or_tiny} short/empty segments detected out of {segment_count}.")

    final_score = max(0.0, min(10.0, round(score, 1)))
    status = "FAIL" if final_score < 5.0 else ("WARN" if final_score < 8.0 else "PASS")

    return EvaluationResult(
        stage="transcription",
        status=status,
        score=final_score,
        issues=issues,
        metrics=metrics,
    )
