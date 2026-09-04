from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.agents.base import AgentContext, BaseAgent
from src.evaluators import judge_summary, refine_summary_with_critique
from src.models import EditorialResult, TranscriptionResult
from src.processing import (
    ProcessingError,
    _format_time,
    _is_narration_complete,
    _prepare_chunk,
    ensure_ollama_running,
    semantic_transcript_chunks,
)
from src.storage import StorageManager

logger = logging.getLogger("EditorialAgent")


class EditorialAgent(BaseAgent):
    """
    Editorial Agent: Autonomous Multi-Turn Local LLM Editor that synthesizes,
    translates, strips promotions, and fact-checks spoken audio summaries.
    Returns typed EditorialResult.
    """

    def __init__(self) -> None:
        super().__init__("EditorialAgent")

    def run(self, context: AgentContext, transcription: TranscriptionResult | None = None) -> EditorialResult:
        output_path = context.working_dir / "narration.json"
        script_path = context.working_dir / "summary.txt"
        cleaned_source_path = context.working_dir / "cleaned_source.txt"

        # Resolve transcript from typed DTO, context, or state
        if transcription:
            transcript = transcription.transcript_data
        elif context.transcription_result:
            transcript = context.transcription_result.transcript_data
        else:
            transcript = context.state.get("transcript_data", {})

        segments = transcript.get("segments", [])
        if not segments:
            raise ProcessingError("No speech segments available for editorial synthesis.")

        # 1. Semantic Chunking respecting natural speech pauses & sentence endings
        chunks = semantic_transcript_chunks(segments, target_characters=4000, maximum_characters=6000)

        # 2. Check verified cached narration
        if output_path.exists():
            try:
                cached = json.loads(output_path.read_text(encoding="utf-8"))
                if _is_narration_complete(cached, len(chunks)):
                    self.log(context, f"Verified complete summary found on disk ({len(chunks)} sections).")
                    script_path.write_text(cached["script"], encoding="utf-8")
                    result = EditorialResult(
                        narration_path=output_path,
                        summary_path=script_path,
                        script=cached["script"],
                        target_language=cached.get("target_language", "English"),
                        critic_score=cached.get("critic_score", 9.0),
                    )
                    context.editorial_result = result
                    return result
                else:
                    StorageManager.safe_delete(output_path)
            except Exception as exc:
                logger.debug(f"Cache check error: {exc}")
                StorageManager.safe_delete(output_path)

        # 3. Ensure Ollama daemon is active
        if not ensure_ollama_running(context.settings):
            raise ProcessingError(f"Ollama server is unreachable at {context.settings.ollama_base_url}")

        target_language = "Tamil" if transcript.get("language", "").lower().startswith("ta") else "English"
        source_language = transcript.get("language", "unknown")

        self.log(context, f"Synthesizing & Fact-Checking {len(chunks)} sections via Local Ollama ({context.settings.ollama_model} -> {target_language})...")

        prepared_chunks = []
        chunk_evaluations = []

        for idx, chunk in enumerate(chunks, start=1):
            start_ts = _format_time(chunk[0].get("start", 0))
            end_ts = _format_time(chunk[-1].get("end", 0))
            print(f"    [{idx}/{len(chunks)}] Section {start_ts} -> {end_ts} [Drafting", end="", flush=True)

            chunk_res = _prepare_chunk(context.settings, context.video, source_language, target_language, chunk)

            # Autonomous Critic Valuation Loop
            source_text = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in chunk)
            draft_script = chunk_res.get("script", "")
            eval_res = judge_summary(context.settings, source_text, draft_script, target_language, context.video.mode)

            if eval_res.status == "FAIL" and eval_res.issues:
                print(f" -> Critic: {eval_res.score}/10 (FAIL) -> Refining", end="", flush=True)
                refined_res = refine_summary_with_critique(
                    context.settings, context.video, source_language, target_language, source_text, draft_script, eval_res.issues
                )
                if refined_res.get("script", "").strip():
                    chunk_res = refined_res
                    eval_res = judge_summary(context.settings, source_text, chunk_res["script"], target_language, context.video.mode)
                    eval_res.retries_used = 1
                    print(f" -> Refined: {eval_res.score}/10 ({eval_res.status})]", flush=True)
                else:
                    print(f" -> Kept Draft ({eval_res.score}/10)]", flush=True)
            else:
                print(f" -> Critic: {eval_res.score}/10 ({eval_res.status})]", flush=True)

            prepared_chunks.append(chunk_res)
            chunk_evaluations.append(eval_res)

        if any(not isinstance(item.get("script"), str) or not item["script"].strip() for item in prepared_chunks):
            raise ProcessingError("Ollama returned empty narration script for one or more sections.")

        avg_critic_score = round(sum(e.score for e in chunk_evaluations) / len(chunk_evaluations), 2) if chunk_evaluations else 9.0
        final_script = "\n\n".join(item["script"].strip() for item in prepared_chunks)

        prepared = {
            "target_language": target_language,
            "script": final_script,
            "cleaned_source": final_script,
            "removed_segments": [removed for item in prepared_chunks for removed in item.get("removed_segments", [])],
            "warnings": [warning for item in prepared_chunks for warning in item.get("warnings", [])],
            "chunk_count": len(prepared_chunks),
            "critic_score": avg_critic_score,
            "evaluations": [
                {"score": e.score, "status": e.status, "issues": e.issues, "metrics": e.metrics, "retries": e.retries_used}
                for e in chunk_evaluations
            ],
        }

        output_path.write_text(json.dumps(prepared, indent=2, ensure_ascii=False), encoding="utf-8")
        script_path.write_text(final_script, encoding="utf-8")
        cleaned_source_path.write_text(final_script, encoding="utf-8")

        self.log(context, f"Editorial Synthesis complete! [Average Critic Score: {avg_critic_score}/10]")

        result = EditorialResult(
            narration_path=output_path,
            summary_path=script_path,
            script=final_script,
            target_language=target_language,
            critic_score=avg_critic_score,
        )
        context.editorial_result = result
        return result
