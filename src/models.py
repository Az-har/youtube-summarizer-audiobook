from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Video:
    video_id: str
    title: str
    url: str
    channel_title: str = ""
    duration_seconds: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def mode(self) -> str:
        return "clean_readaloud" if self.duration_seconds < 20 * 60 else "detailed_synthesis"


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    completed_file: Path
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:14b"
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    tts_provider: str = "command"
    tts_command_template: str = ""
    tts_voice_tamil: str = ""
    tts_voice_english: str = ""
    ffmpeg_binary: str = "ffmpeg"
    ytdlp_binary: str = "yt-dlp"


@dataclass
class EvaluationResult:
    stage: str
    status: str  # "PASS", "WARN", "FAIL"
    score: float  # 0.0 to 10.0
    issues: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    retries_used: int = 0


@dataclass
class QualityScorecard:
    video_id: str
    overall_status: str = "PASS"
    overall_score: float = 10.0
    stages: dict[str, EvaluationResult] = field(default_factory=dict)

    def add_result(self, result: EvaluationResult) -> None:
        self.stages[result.stage] = result
        if result.status == "FAIL":
            self.overall_status = "FAIL"
        elif result.status == "WARN" and self.overall_status != "FAIL":
            self.overall_status = "WARN"
        scores = [r.score for r in self.stages.values() if r.score > 0]
        if scores:
            self.overall_score = round(sum(scores) / len(scores), 2)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "overall_status": self.overall_status,
            "overall_score": self.overall_score,
            "stages": {
                k: {
                    "status": v.status,
                    "score": v.score,
                    "issues": v.issues,
                    "metrics": v.metrics,
                    "retries_used": v.retries_used,
                }
                for k, v in self.stages.items()
            },
        }


@dataclass
class ProcessResult:
    video: Video
    status: str
    message: str = ""
    transcript_path: str = ""
    summary_path: str = ""
    audio_path: str = ""
    warnings: list[str] = field(default_factory=list)
    scorecard: QualityScorecard | None = None

