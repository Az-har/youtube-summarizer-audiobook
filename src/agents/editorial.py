from __future__ import annotations

import json
import logging
import re
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
    chapter_aware_transcript_chunks,
    ensure_ollama_running,
    semantic_transcript_chunks,
)
from src.storage import StorageManager
from src.text_cleaner import stitch_narrative_continuity
from src.vram_guard import VRAMGuard

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

        # Resolve transcript from typed DTO with fallback to context.state
        trans_res = transcription or context.transcription_result
        transcript = (trans_res.transcript_data if trans_res else {}) or context.state.get("transcript_data", {})

        segments = transcript.get("segments", [])
        if not segments:
            raise ProcessingError("No speech segments available for editorial synthesis.")

        # Resolve chapter markers from typed IngestionResult, context.state, or video metadata
        chapters = []
        if context.ingestion_result and context.ingestion_result.chapters:
            chapters = context.ingestion_result.chapters
        elif context.state.get("chapters"):
            chapters = context.state.get("chapters")
        elif getattr(context.video, "chapters", None):
            chapters = context.video.chapters
        elif context.video and context.video.raw and context.video.raw.get("chapters"):
            chapters = context.video.raw.get("chapters", [])

        # 1. Chapter-Aware & Semantic Chunking respecting topic units and speech pauses
        chunks = chapter_aware_transcript_chunks(
            segments,
            chapters=chapters,
            target_characters=4000,
            maximum_characters=6000,
            pause_threshold=1.5,
        )

        # 2. Check verified cached narration via StorageManager
        cached = StorageManager.is_narration_cached(output_path, len(chunks))
        if cached:
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

        # 3. Ensure Ollama daemon is active
        if not ensure_ollama_running(context.settings):
            raise ProcessingError(
                f"Ollama server is not reachable at {context.settings.ollama_base_url}. "
                "Please ensure Ollama is installed and running."
            )

        # Determine target language: explicit user preference from Notion/Video overrides auto-detection
        req_lang = (getattr(context.video, "requested_language", "") or "").lower().strip()
        if req_lang in ("tamil", "ta"):
            target_language = "Tamil"
        elif req_lang in ("english", "en"):
            target_language = "English"
        elif req_lang and req_lang not in ("auto", "none"):
            target_language = req_lang.capitalize()
        else:
            target_language = "Tamil" if transcript.get("language", "").lower().startswith("ta") else "English"

        source_language = transcript.get("language", "unknown")

        self.log(context, f"Synthesizing & Fact-Checking {len(chunks)} sections via Local Ollama ({context.settings.ollama_model} -> {target_language})...")

        prepared_chunks = []
        chunk_evaluations = []
        previous_tail = ""

        for idx, chunk in enumerate(chunks, start=1):
            start_ts = _format_time(chunk[0].get("start", 0))
            end_ts = _format_time(chunk[-1].get("end", 0))
            chapter_title = chunk[0].get("_chapter_title", "")
            print(f"    [{idx}/{len(chunks)}] Section {start_ts} -> {end_ts} [Drafting", end="", flush=True)

            with VRAMGuard.guard("Ollama_Editorial"):
                chunk_res = _prepare_chunk(
                    context.settings,
                    context.video,
                    source_language,
                    target_language,
                    chunk,
                    section_idx=idx,
                    total_sections=len(chunks),
                    chapter_title=chapter_title,
                    previous_section_tail=previous_tail,
                )

                # Autonomous Critic Valuation Loop
                source_text = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in chunk)
                draft_script = chunk_res.get("script", "")
                eval_res = judge_summary(context.settings, source_text, draft_script, target_language, context.video.mode)

                if eval_res.status == "FAIL" and eval_res.issues:
                    print(f" -> Critic: {eval_res.score}/10 (FAIL) -> Refining", end="", flush=True)
                    refined_res = refine_summary_with_critique(
                        context.settings,
                        context.video,
                        source_language,
                        target_language,
                        source_text,
                        draft_script,
                        eval_res.issues,
                        previous_section_tail=previous_tail,
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

            # Update rolling previous_tail for the subsequent section's continuation
            script_text = chunk_res.get("script", "").strip()
            if script_text:
                sentences = re.split(r'(?<=[.!?])\s+', script_text)
                previous_tail = " ".join(sentences[-2:]).strip() if len(sentences) >= 2 else script_text
                if len(previous_tail) > 250:
                    previous_tail = previous_tail[-250:].strip()

            prepared_chunks.append(chunk_res)
            chunk_evaluations.append(eval_res)

        if any(not isinstance(item.get("script"), str) or not item["script"].strip() for item in prepared_chunks):
            raise ProcessingError("Ollama returned empty narration script for one or more sections.")

        avg_critic_score = round(sum(e.score for e in chunk_evaluations) / len(chunk_evaluations), 2) if chunk_evaluations else 9.0
        
        # Pre-TTS Narrative Continuity Stitcher & Prosody Normalization
        raw_scripts = [item["script"].strip() for item in prepared_chunks if item.get("script")]
        final_script = stitch_narrative_continuity(raw_scripts)

        # Aggregate Second Brain knowledge structures from chunk results
        theses = [item["core_thesis"].strip() for item in prepared_chunks if item.get("core_thesis")]
        aggregated_thesis = " ".join(theses) if theses else (final_script[:350] + "..." if len(final_script) > 350 else final_script)
        aggregated_models = [m for item in prepared_chunks for m in item.get("mental_models", []) if m]
        aggregated_actions = [a for item in prepared_chunks for a in item.get("actionable_takeaways", []) if a]

        prepared = {
            "target_language": target_language,
            "script": final_script,
            "cleaned_source": final_script,
            "core_thesis": aggregated_thesis,
            "mental_models": aggregated_models,
            "actionable_takeaways": aggregated_actions,
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
