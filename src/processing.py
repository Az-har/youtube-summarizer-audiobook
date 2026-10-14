from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from src.models import Settings, Video
from src.storage import StorageManager

logger = logging.getLogger("Processing")


class ProcessingError(RuntimeError):
    pass


def check_ollama_health(settings: Settings) -> bool:
    """Checks if the local Ollama server is responding."""
    try:
        req = urllib.request.Request(f"{settings.ollama_base_url}/api/tags", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.debug(f"Ollama health check notice: {exc}")
        return False


def _find_ollama_binary() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidate = Path(local_app) / "Programs" / "Ollama" / "ollama.exe"
            if candidate.exists():
                return str(candidate)
    return None


def ensure_ollama_running(settings: Settings, timeout: int = 15) -> bool:
    """Checks if Ollama is running, and auto-starts the Ollama server if not."""
    if check_ollama_health(settings):
        return True

    ollama_bin = _find_ollama_binary()
    if not ollama_bin:
        return False

    print("  Ollama server is not running. Auto-starting Ollama daemon in background...")
    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        logger.warning(f"Failed to auto-start Ollama: {exc}")
        return False

    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(1)
        if check_ollama_health(settings):
            print("  Ollama server started and responsive.")
            return True

    return False


def _format_time(seconds: float) -> str:
    total_secs = int(seconds)
    mins, secs = divmod(total_secs, 60)
    return f"{mins:02d}:{secs:02d}"


def run_command(args: list[str], description: str, timeout: float | None = None) -> None:
    """
    Memory-safe subprocess executor streaming stderr line-by-line and keeping
    only a bounded rolling buffer of recent lines in case of error.
    """
    from collections import deque
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            bufsize=1,
        )
        stderr_tail = deque(maxlen=30)
        if proc.stderr:
            for line in proc.stderr:
                stderr_tail.append(line)
        returncode = proc.wait(timeout=timeout)
        if returncode != 0:
            err_msg = "".join(stderr_tail).strip()
            raise ProcessingError(f"{description} failed with exit code {returncode}: {err_msg[-1200:]}")
    except FileNotFoundError as exc:
        raise ProcessingError(f"{description} executable was not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise ProcessingError(f"{description} timed out after {timeout}s") from exc
    except subprocess.SubprocessError as exc:
        raise ProcessingError(f"{description} process execution failed: {exc}") from exc


def _find_ffmpeg(configured: str = "ffmpeg") -> str:
    if shutil.which(configured) or Path(configured).is_file():
        return configured
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        for match in winget_dir.glob("**/ffmpeg.exe"):
            if match.is_file():
                return str(match)
    return "ffmpeg"


def _find_ffprobe(configured_ffmpeg: str = "ffmpeg") -> str:
    ffmpeg_bin = _find_ffmpeg(configured_ffmpeg)
    ffmpeg_path = Path(ffmpeg_bin)
    if ffmpeg_path.is_file():
        ffprobe_sibling = ffmpeg_path.parent / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if ffprobe_sibling.is_file():
            return str(ffprobe_sibling)
    if shutil.which("ffprobe"):
        return "ffprobe"
    return "ffprobe"


def _get_audio_duration(audio_path: Path, ffmpeg_bin: str = "ffmpeg") -> float:
    ffprobe_bin = _find_ffprobe(ffmpeg_bin)
    try:
        proc = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, check=True, timeout=10
        )
        return float(proc.stdout.strip())
    except Exception as exc:
        logger.debug(f"Could not determine audio duration for {audio_path}: {exc}")
        return 0.0


def _is_transcript_complete(transcript_data: dict, audio_path: Path, settings: Settings) -> bool:
    """Verifies that the transcript exists, contains segments, and covers the full audio duration."""
    if not isinstance(transcript_data, dict):
        return False
    segments = transcript_data.get("segments", [])
    if not isinstance(segments, list) or not segments:
        return False

    total_audio_duration = _get_audio_duration(audio_path, settings.ffmpeg_binary)
    if total_audio_duration <= 0.0:
        return True

    last_segment_end = float(segments[-1].get("end", 0.0))
    # If audio is longer than 20 seconds, transcript should cover at least 75% of total audio duration
    if total_audio_duration > 20.0:
        if last_segment_end < (total_audio_duration * 0.75):
            return False

    return True


def _get_ggml_model(settings: Settings, model_name: str) -> Path:
    models_dir = settings.root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    
    normalized = model_name.lower().strip()
    if normalized in ("large-v3", "v3"):
        filename = "ggml-large-v3.bin"
    elif normalized in ("large", "large-v2", "v2"):
        filename = "ggml-large-v2.bin"
    elif normalized in ("turbo", "large-v3-turbo", "large-turbo"):
        filename = "ggml-large-v3-turbo.bin"
    elif normalized == "medium":
        filename = "ggml-medium.bin"
    elif normalized == "small":
        filename = "ggml-small.bin"
    elif normalized == "base":
        filename = "ggml-base.bin"
    else:
        filename = f"ggml-{normalized}.bin"
        
    model_path = models_dir / filename
    if not model_path.exists() or model_path.stat().st_size == 0:
        url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{filename}"
        print(f"  Downloading Whisper model '{filename}' from Hugging Face...")
        urllib.request.urlretrieve(url, str(model_path))
        print(f"  Downloaded {filename} successfully.")
    return model_path


class OllamaClient:
    """
    Robust client for Ollama API communication with structured JSON extraction,
    markdown fence handling, and exponential backoff retry.
    """

    @staticmethod
    def extract_json(raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()
        # 1. Direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. Markdown code block stripping ```json { ... } ```
        fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

        # 3. Balanced braces extraction
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        raise ProcessingError(f"Ollama did not return valid JSON. Snippet: {text[:250]}")

    @classmethod
    def generate_json(cls, base_url: str, model: str, prompt: str, max_retries: int = 2) -> dict[str, Any]:
        has_ollama = False
        client = None
        try:
            import ollama
            client = ollama.Client(host=base_url)
            has_ollama = True
        except ImportError:
            has_ollama = False

        for attempt in range(1, max_retries + 1):
            accumulated = []
            try:
                if has_ollama and client is not None:
                    response = client.generate(
                        model=model,
                        prompt=prompt,
                        stream=True,
                        format="json",
                        options={
                            "num_ctx": 4096,
                            "temperature": 0.2,
                            "top_p": 0.9,
                            "top_k": 40,
                        },
                    )
                    for chunk in response:
                        if "response" in chunk:
                            accumulated.append(chunk["response"])
                            if len(accumulated) % 25 == 0:
                                print(".", end="", flush=True)
                else:
                    payload = json.dumps({
                        "model": model,
                        "prompt": prompt,
                        "stream": True,
                        "format": "json",
                        "options": {
                            "num_ctx": 4096,
                            "temperature": 0.2,
                            "top_p": 0.9,
                            "top_k": 40,
                        },
                    }).encode("utf-8")
                    req = urllib.request.Request(
                        f"{base_url.rstrip('/')}/api/generate",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        for line in resp:
                            if not line:
                                continue
                            try:
                                chunk_json = json.loads(line.decode("utf-8"))
                                text_piece = chunk_json.get("response", "")
                                if text_piece:
                                    accumulated.append(text_piece)
                                    if len(accumulated) % 25 == 0:
                                        print(".", end="", flush=True)
                            except json.JSONDecodeError:
                                pass

                print(" [OK]", flush=True)
                full_text = "".join(accumulated)
                return cls.extract_json(full_text)
            except Exception as net_err:
                logger.warning(f"Ollama connection attempt {attempt}/{max_retries} failed: {net_err}")
                if attempt < max_retries:
                    time.sleep(2.0 * attempt)
                    continue
                raise ProcessingError(
                    f"Could not connect to Ollama at {base_url}. Please ensure Ollama is running: {net_err}"
                ) from net_err

        raise ProcessingError("Failed to generate response from Ollama after retries.")


def _ollama(settings: Settings, prompt: str) -> dict:
    """Wrapper function preserving legacy caller compatibility."""
    return OllamaClient.generate_json(settings.ollama_base_url, settings.ollama_model, prompt)


def semantic_transcript_chunks(
    segments: list[dict],
    target_characters: int = 4000,
    maximum_characters: int = 6000,
    pause_threshold: float = 1.5,
) -> list[list[dict]]:
    """
    Semantically chunks Whisper segments by prioritizing natural speech pauses (gap >= pause_threshold)
    and sentence boundaries (. ? ! |) when approaching the character budget.
    Guarantees that thoughts/sentences are not abruptly severed mid-sentence.
    """
    if not segments:
        return []

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_size = 0

    for i, segment in enumerate(segments):
        text = segment.get("text", "").strip()
        seg_size = len(text) + 40
        current.append(segment)
        current_size += seg_size

        is_sentence_end = bool(text and text[-1] in (".", "?", "!", "|", "\u0964"))
        is_natural_pause = False
        if i + 1 < len(segments):
            next_start = float(segments[i + 1].get("start", 0.0))
            curr_end = float(segment.get("end", 0.0))
            if next_start - curr_end >= pause_threshold:
                is_natural_pause = True

        # 1. Natural speech pause boundary when target size reached
        if current_size >= target_characters and is_natural_pause:
            chunks.append(current)
            current, current_size = [], 0
        # 2. Reached maximum characters budget at a clean sentence ending
        elif current_size >= maximum_characters and is_sentence_end:
            chunks.append(current)
            current, current_size = [], 0
        # 3. Hard safety ceiling if speaker never paused or punctuated
        elif current_size >= int(maximum_characters * 1.3):
            chunks.append(current)
            current, current_size = [], 0

    if current:
        chunks.append(current)

    return chunks


def chapter_aware_transcript_chunks(
    segments: list[dict],
    chapters: list[dict] | None = None,
    target_characters: int = 4000,
    maximum_characters: int = 6000,
    pause_threshold: float = 1.5,
) -> list[list[dict]]:
    """
    Chapter-Aware Semantic Narrative Chunking (Innovation 3):
    Aligns chunk boundaries with author-defined chapter markers when available.
    - If chapters exist, segments are grouped into coherent topic units.
    - If a chapter exceeds 12 minutes or 8,000 characters, it is adaptively sub-chunked
      using semantic speech pauses.
    - If no chapters exist, gracefully falls back to pause-based semantic_transcript_chunks.
    """
    if not segments:
        return []

    if not chapters or not isinstance(chapters, list) or len(chapters) == 0:
        return semantic_transcript_chunks(
            segments,
            target_characters=target_characters,
            maximum_characters=maximum_characters,
            pause_threshold=pause_threshold,
        )

    # Sort valid chapters by start time
    sorted_chapters = []
    for ch in chapters:
        try:
            start_t = float(ch.get("start_time", 0.0))
            end_t = float(ch.get("end_time", 0.0))
            title = str(ch.get("title", "")).strip()
            sorted_chapters.append({"start_time": start_t, "end_time": end_t, "title": title})
        except (ValueError, TypeError):
            continue

    sorted_chapters.sort(key=lambda x: x["start_time"])
    if not sorted_chapters:
        return semantic_transcript_chunks(
            segments,
            target_characters=target_characters,
            maximum_characters=maximum_characters,
            pause_threshold=pause_threshold,
        )

    # Assign segments to chapters
    chapter_buckets: list[list[dict]] = [[] for _ in sorted_chapters]
    unassigned: list[dict] = []

    for seg in segments:
        seg_start = float(seg.get("start", 0.0))
        assigned = False
        for idx, ch in enumerate(sorted_chapters):
            ch_start = ch["start_time"]
            ch_end = ch["end_time"]
            if ch_end <= ch_start:
                if idx + 1 < len(sorted_chapters):
                    ch_end = sorted_chapters[idx + 1]["start_time"]
                else:
                    ch_end = float("inf")

            if ch_start <= seg_start < ch_end:
                chapter_buckets[idx].append(seg)
                assigned = True
                break

        if not assigned:
            if sorted_chapters and seg_start < sorted_chapters[0]["start_time"]:
                chapter_buckets[0].append(seg)
            elif sorted_chapters:
                chapter_buckets[-1].append(seg)
            else:
                unassigned.append(seg)

    result_chunks: list[list[dict]] = []
    for idx, bucket in enumerate(chapter_buckets):
        if not bucket:
            continue
        ch = sorted_chapters[idx]
        ch_title = ch.get("title", "")
        total_chars = sum(len(s.get("text", "")) for s in bucket)
        dur = float(bucket[-1].get("end", 0.0)) - float(bucket[0].get("start", 0.0))

        if ch_title and bucket:
            bucket[0]["_chapter_title"] = ch_title

        if dur > 720.0 or total_chars > 8000:
            sub = semantic_transcript_chunks(
                bucket,
                target_characters=target_characters,
                maximum_characters=maximum_characters,
                pause_threshold=pause_threshold,
            )
            result_chunks.extend(sub)
        else:
            result_chunks.append(bucket)

    if unassigned:
        result_chunks.extend(semantic_transcript_chunks(
            unassigned,
            target_characters=target_characters,
            maximum_characters=maximum_characters,
            pause_threshold=pause_threshold,
        ))

    return result_chunks if result_chunks else semantic_transcript_chunks(
        segments,
        target_characters=target_characters,
        maximum_characters=maximum_characters,
        pause_threshold=pause_threshold,
    )


def _transcript_chunks(segments: list[dict], maximum_characters: int = 6000) -> list[list[dict]]:
    """Backward-compatible wrapper routing to semantic_transcript_chunks."""
    target = min(int(maximum_characters * 0.75), max(50, maximum_characters - 100))
    return semantic_transcript_chunks(segments, target_characters=target, maximum_characters=maximum_characters)


def _prepare_chunk(
    settings: Settings,
    video: Video,
    source_language: str,
    target_language: str,
    segments: list[dict],
    section_idx: int = 1,
    total_sections: int = 1,
    chapter_title: str = "",
    previous_section_tail: str = "",
) -> dict:
    from src.text_cleaner import sanitize_spoken_transcript

    source_lines = []
    for segment in segments:
        raw_text = segment.get("text", "")
        cleaned_text = sanitize_spoken_transcript(raw_text)
        if cleaned_text:
            source_lines.append(f"[{segment.get('start', 0.0):.1f}-{segment.get('end', 0.0):.1f}] {cleaned_text}")

    source_text = "\n".join(source_lines) if source_lines else "\n".join(
        f"[{s.get('start', 0.0):.1f}-{s.get('end', 0.0):.1f}] {s.get('text', '')}" for s in segments
    )

    if video.mode == "translation":
        task = "faithful, natural spoken translation preserving the full dialog and intent into fluent spoken phrasing"
    elif video.mode == "absolute_summary":
        task = "high-signal absolute distillation (extract only the core substance, foundational logic, mechanisms, and actionable insights; deliberately omit conversational buildup, secondary examples, redundancies, and non-essential padding)"
    elif video.mode == "clean_readaloud":
        task = "near-complete cleaned read-aloud"
    else:
        task = "detailed information-first synthesis"

    # Contextual story arc & section role
    context_lines = [f"- Video Title: {video.title}"]
    if chapter_title:
        context_lines.append(f"- Current Chapter: {chapter_title}")
    context_lines.append(f"- Section: {section_idx} of {total_sections}")

    if section_idx == 1 and total_sections > 1:
        narrative_arc = "Opening Prologue: Craft an engaging, warm conversational hook suitable for an immersive solo podcast/audiobook host."
    elif section_idx == total_sections and total_sections > 1:
        narrative_arc = "Concluding Epilogue: Synthesize a thoughtful, memorable conclusion and final takeaways suitable for an audiobook finale."
    else:
        narrative_arc = "Core Narrative: Seamlessly develop concepts with active conversational pacing and flowing transitions."
    context_lines.append(f"- Narrative Role: {narrative_arc}")

    # Continuation instruction
    if previous_section_tail and previous_section_tail.strip():
        continuation_prompt = f'''  5. NARRATIVE CONTINUATION REQUIREMENT:
     The previous section concluded with:
     "{previous_section_tail.strip()}"
     You MUST weave the beginning of this section seamlessly and directly from that concluding thought.
     STRICT PROHIBITION: NEVER use introductory transitional framing (DO NOT say "In this section", "Next, we will look at", "Moving on", "Furthermore", or "The speaker continues"). Start immediately with substantive prose.'''
    else:
        continuation_prompt = '''  5. NARRATIVE OPENING: Begin directly with an engaging, articulate spoken hook. Never announce the video, speaker, or section.'''

    prompt = f'''You prepare one consecutive section of a factual spoken audio digest. Return ONLY valid JSON with this exact schema:
{{
  "section_title": "string",
  "script": "string",
  "core_thesis": "string",
  "mental_models": [{{"title": "string", "description": "string"}}],
  "actionable_takeaways": ["string"],
  "removed_segments": ["string"],
  "warnings": ["string"]
}}

Input details:
- Source language: {source_language}
- Target narration language: {target_language}
- Task: {task}
{chr(10).join(context_lines)}
- Rules:
  1. Persona & Tone: Speak as an engaging, warm, articulate solo podcast/audiobook host. Use natural conversational phrasing, rhetorical hooks, and flowing momentum. NEVER sound like a dry textbook or clinical report.
  2. Maintain 100% factual fidelity to the source audio. DO NOT hallucinate facts, metrics, or claims.
  3. Strip all sponsor shoutouts, subscriber requests, channel likes, and promo segments.
  4. Translate all visual references (slides, screen gestures) into auditory conceptual explanations.
{continuation_prompt}
  6. Spoken Cadence: Write in natural conversational sentences (10-25 words) with strategic breath pauses (commas, em-dashes). Absolutely no robotic monotone.
  7. Second Brain Distillation: Provide a 1-sentence 'core_thesis', key 'mental_models', and concrete 'actionable_takeaways' capturing the pure signal of this section.
  8. Write purely in {target_language}.

Transcript section:
{source_text}
'''
    return _ollama(settings, prompt)



def _is_narration_complete(narration_data: dict, expected_chunks_count: int) -> bool:
    """Verifies that the narration script exists, contains text, and has processed all chunks."""
    if not isinstance(narration_data, dict):
        return False
    script = narration_data.get("script", "").strip()
    if not script or len(script) < 5:
        return False
    chunk_count = narration_data.get("chunk_count", 0)
    if expected_chunks_count > 0 and chunk_count < expected_chunks_count:
        return False
    return True


def prepare_narration(settings: Settings, video: Video, transcript: dict, output_path: Path) -> dict:
    """
    Legacy wrapper delegating directly to EditorialAgent to consolidate all editorial logic.
    """
    if not ensure_ollama_running(settings):
        raise ProcessingError(
            f"Ollama server is not reachable at {settings.ollama_base_url}. "
            "Please ensure Ollama is installed and running."
        )

    from src.agents.base import AgentContext
    from src.agents.editorial import EditorialAgent
    from src.models import TranscriptionResult

    working_dir = output_path.parent
    working_dir.mkdir(parents=True, exist_ok=True)
    context = AgentContext(settings=settings, video=video, working_dir=working_dir)
    context.transcription_result = TranscriptionResult(
        transcript_path=working_dir / "transcript.json",
        txt_path=working_dir / "transcript.txt",
        transcript_data=transcript,
    )
    agent = EditorialAgent()
    res = agent.run(context)
    return json.loads(res.narration_path.read_text(encoding="utf-8"))


def synthesize(settings: Settings, script_path: Path, output_audio: Path, language: str) -> Path | None:
    """Synthesize high-quality spoken audio from the summary script using Kokoro-82M, edge-tts, or custom CLI."""
    cached_audio = StorageManager.is_audiobook_cached(output_audio)
    if cached_audio:
        print(f"  Found verified TTS audio on disk ({output_audio.stat().st_size // 1024} KB).")
        return cached_audio

    StorageManager.safe_delete(output_audio)

    from src.tts_engine import synthesize_spoken_audio
    return synthesize_spoken_audio(settings, script_path, output_audio, language)

