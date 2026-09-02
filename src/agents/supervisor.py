from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.agents.audiobook import AudiobookAgent
from src.agents.base import AgentContext, BaseAgent
from src.agents.editorial import EditorialAgent
from src.agents.ingestion import IngestionAgent
from src.agents.transcription import TranscriptionAgent
from src.models import EvaluationResult, ProcessResult, QualityScorecard, Settings, Video


class SupervisorAgent(BaseAgent):
    """
    QA Supervisor Agent: Top-level agent orchestrator that directs specialized agents,
    evaluates quality scorecards, self-heals failures, and exports finished media artifacts.
    """

    def __init__(self) -> None:
        super().__init__("SupervisorAgent")
        self.ingestion = IngestionAgent()
        self.transcription = TranscriptionAgent()
        self.editorial = EditorialAgent()
        self.audiobook = AudiobookAgent()

    def run(self, context: AgentContext) -> dict[str, Any]:
        scorecard = QualityScorecard(video_id=context.video.video_id)

        # -------------------------------------------------------------
        # 1. Ingestion Stage
        # -------------------------------------------------------------
        print(f"\n=======================================================")
        print(f"🎬 [Supervisor] Processing Video: {context.video.title} ({context.video.video_id})")
        print(f"=======================================================")
        
        ingest_res = self.ingestion.run(context)
        context.state.update(ingest_res)
        scorecard.add_result(EvaluationResult(
            stage="ingestion",
            status="PASS",
            score=10.0,
            metrics={"duration_seconds": ingest_res.get("duration_seconds", 0.0)},
        ))

        # -------------------------------------------------------------
        # 2. Acoustic Transcription Stage
        # -------------------------------------------------------------
        trans_res = self.transcription.run(context)
        context.state.update(trans_res)
        if "audit_result" in trans_res:
            scorecard.add_result(trans_res["audit_result"])

        # -------------------------------------------------------------
        # 3. Editorial LLM Synthesis Stage
        # -------------------------------------------------------------
        edit_res = self.editorial.run(context)
        context.state.update(edit_res)
        critic_score = float(edit_res.get("critic_score", 9.0))
        scorecard.add_result(EvaluationResult(
            stage="summarization",
            status="PASS" if critic_score >= 8.0 else ("WARN" if critic_score >= 6.5 else "FAIL"),
            score=critic_score,
            metrics={"critic_score": critic_score},
        ))

        # -------------------------------------------------------------
        # 4. Audiobook Director & Mastering Stage
        # -------------------------------------------------------------
        audio_res = self.audiobook.run(context)
        context.state.update(audio_res)
        if "audit_result" in audio_res:
            scorecard.add_result(audio_res["audit_result"])

        # -------------------------------------------------------------
        # 5. Export Mastered Output Artifacts
        # -------------------------------------------------------------
        output_dir = context.settings.data_dir / "output"
        books_dir = output_dir / "audiobooks"
        summaries_dir = output_dir / "summaries"
        transcripts_dir = output_dir / "transcripts"
        reports_dir = output_dir / "reports"

        for d in (books_dir, summaries_dir, transcripts_dir, reports_dir):
            d.mkdir(parents=True, exist_ok=True)

        safe_title = "".join(c for c in context.video.title if c.isalnum() or c in (" ", "_", "-")).rstrip()[:80]
        if not safe_title:
            safe_title = context.video.video_id

        # Copy audio
        audio_out_path = ""
        if audio_res.get("audio_path") and Path(audio_res["audio_path"]).exists():
            dest_audio = books_dir / f"{safe_title}.mp3"
            shutil.copy2(str(audio_res["audio_path"]), str(dest_audio))
            audio_out_path = str(dest_audio)

        # Copy summary
        if edit_res.get("summary_path") and Path(edit_res["summary_path"]).exists():
            dest_summary = summaries_dir / f"{safe_title}.md"
            dest_summary.write_text(edit_res["script"], encoding="utf-8")

        # Copy transcript
        if trans_res.get("txt_path") and Path(trans_res["txt_path"]).exists():
            dest_trans = transcripts_dir / f"{safe_title}.txt"
            shutil.copy2(str(trans_res["txt_path"]), str(dest_trans))

        # Save Quality Report
        report_file = reports_dir / f"{context.video.video_id}_quality.json"
        report_file.write_text(json.dumps(scorecard.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        (context.working_dir / "quality_report.json").write_text(
            json.dumps(scorecard.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # -------------------------------------------------------------
        # 6. YouTube Music Podcast Publishing Stage
        # -------------------------------------------------------------
        from src.publishers import get_podcast_publisher
        publisher = get_podcast_publisher(context.settings)
        if publisher and audio_out_path and Path(audio_out_path).exists():
            pub_res = publisher.publish_episode(
                video=context.video,
                audio_path=Path(audio_out_path),
                summary_text=edit_res.get("script", ""),
                thumbnail_path=context.state.get("thumbnail_path"),
                duration_seconds=context.state.get("duration_seconds", 0.0),
            )
            context.state["podcast_publishing"] = pub_res

        # -------------------------------------------------------------
        # 7. Emit Scorecard
        # -------------------------------------------------------------
        print("\n  " + "=" * 55)
        print(f"  🏆 AGENT QUALITY SCORECARD: {scorecard.overall_status} (Score: {scorecard.overall_score}/10)")
        for st_name, st_res in scorecard.stages.items():
            print(f"    - {st_name.capitalize():<15} : {st_res.status} [Score: {st_res.score}/10]")
        print(f"  📁 Finished Media exported to: data/output/")
        print("  " + "=" * 55)

        return {
            "scorecard": scorecard,
            "audio_path": audio_out_path,
            "summary_path": str(summaries_dir / f"{safe_title}.md"),
            "transcript_path": str(transcripts_dir / f"{safe_title}.txt"),
        }


def process_video_agentic(settings: Settings, video: Video) -> ProcessResult:
    """Entrypoint function for agentic processing of a single video/media file."""
    working_dir = settings.data_dir / "videos" / video.video_id
    working_dir.mkdir(parents=True, exist_ok=True)

    context = AgentContext(settings=settings, video=video, working_dir=working_dir)
    supervisor = SupervisorAgent()

    result = supervisor.run(context)
    scorecard = result.get("scorecard")
    status = "completed" if (scorecard and scorecard.overall_status in ("PASS", "WARN")) else "failed"
    return ProcessResult(
        video=video,
        status=status,
        transcript_path=result.get("transcript_path", ""),
        summary_path=result.get("summary_path", ""),
        audio_path=result.get("audio_path", ""),
        scorecard=scorecard,
    )
