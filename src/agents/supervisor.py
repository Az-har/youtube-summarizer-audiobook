from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.agents.audiobook import AudiobookAgent
from src.agents.base import AgentContext, BaseAgent
from src.agents.editorial import EditorialAgent
from src.agents.ingestion import IngestionAgent
from src.agents.transcription import TranscriptionAgent
from src.models import (
    AudiobookResult,
    EditorialResult,
    EvaluationResult,
    IngestionResult,
    ProcessResult,
    QualityScorecard,
    Settings,
    TranscriptionResult,
    Video,
)
from src.storage import StorageManager

logger = logging.getLogger("SupervisorAgent")


class SupervisorAgent(BaseAgent):
    """
    QA Supervisor Agent: Top-level agent orchestrator that directs specialized agents
    using strongly typed DTO contracts, evaluates quality scorecards, self-heals,
    and delegates artifact exporting to StorageManager.
    """

    def __init__(self) -> None:
        super().__init__("SupervisorAgent")
        self.ingestion = IngestionAgent()
        self.transcription = TranscriptionAgent()
        self.editorial = EditorialAgent()
        self.audiobook = AudiobookAgent()

    def run(self, context: AgentContext) -> dict[str, Any]:
        scorecard = QualityScorecard(video_id=context.video.video_id)

        print(f"\n=======================================================")
        print(f"🎬 [Supervisor] Processing Video: {context.video.title} ({context.video.video_id})")
        print(f"=======================================================")

        # -------------------------------------------------------------
        # 1. Ingestion Stage (Typed IngestionResult)
        # -------------------------------------------------------------
        ingest_res: IngestionResult = self.ingestion.run(context)
        context.ingestion_result = ingest_res
        context.state.update(ingest_res.to_dict())
        scorecard.add_result(EvaluationResult(
            stage="ingestion",
            status="PASS",
            score=10.0,
            metrics={"duration_seconds": ingest_res.duration_seconds},
        ))

        # -------------------------------------------------------------
        # 2. Acoustic Transcription Stage (Typed TranscriptionResult)
        # -------------------------------------------------------------
        trans_res: TranscriptionResult = self.transcription.run(context, ingestion=ingest_res)
        context.transcription_result = trans_res
        context.state.update(trans_res.to_dict())
        if trans_res.audit_result:
            scorecard.add_result(trans_res.audit_result)

        # -------------------------------------------------------------
        # 3. Editorial LLM Synthesis Stage (Typed EditorialResult)
        # -------------------------------------------------------------
        edit_res: EditorialResult = self.editorial.run(context, transcription=trans_res)
        context.editorial_result = edit_res
        context.state.update(edit_res.to_dict())
        critic_score = edit_res.critic_score
        scorecard.add_result(EvaluationResult(
            stage="summarization",
            status="PASS" if critic_score >= 8.0 else ("WARN" if critic_score >= 6.5 else "FAIL"),
            score=critic_score,
            metrics={"critic_score": critic_score},
        ))

        # -------------------------------------------------------------
        # 4. Audiobook Director & Mastering Stage (Typed AudiobookResult)
        # -------------------------------------------------------------
        audio_res: AudiobookResult = self.audiobook.run(context, editorial=edit_res, ingestion=ingest_res)
        context.audiobook_result = audio_res
        context.state.update(audio_res.to_dict())
        if audio_res.audit_result:
            scorecard.add_result(audio_res.audit_result)

        # -------------------------------------------------------------
        # 5. Export Mastered Output Artifacts via StorageManager
        # -------------------------------------------------------------
        exported = StorageManager.export_finished_media(
            settings=context.settings,
            video_id=context.video.video_id,
            title=context.video.title,
            audio_src=audio_res.audio_path,
            script=edit_res.script,
            transcript_txt_src=trans_res.txt_path,
            scorecard=scorecard,
            working_dir=context.working_dir,
        )

        audio_out_path = exported.get("audio_path", "")

        # -------------------------------------------------------------
        # 6. YouTube Music Podcast Publishing Stage
        # -------------------------------------------------------------
        from src.publishers import get_podcast_publisher
        publisher = get_podcast_publisher(context.settings)
        if publisher and audio_out_path and Path(audio_out_path).exists():
            pub_res = publisher.publish_episode(
                video=context.video,
                audio_path=Path(audio_out_path),
                summary_text=edit_res.script,
                thumbnail_path=ingest_res.thumbnail_path,
                duration_seconds=ingest_res.duration_seconds,
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

        # -------------------------------------------------------------
        # 7. Intermediate Artifact Cleanup Hook (Disk Retention Policy)
        # -------------------------------------------------------------
        if getattr(context.settings, "clean_intermediates", False):
            cleaned = StorageManager.clean_intermediate_artifacts(context.working_dir)
            if cleaned:
                self.log(context, f"Cleaned {len(cleaned)} intermediate artifacts: {', '.join(cleaned)}")

        print("  " + "=" * 55)

        return {
            "scorecard": scorecard,
            "audio_path": audio_out_path,
            "summary_path": exported.get("summary_path", ""),
            "transcript_path": exported.get("transcript_path", ""),
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
