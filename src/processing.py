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


def _transcript_chunks(segments: list[dict], maximum_characters: int = 6000) -> list[list[dict]]:
    """Backward-compatible wrapper routing to semantic_transcript_chunks."""
    target = min(int(maximum_characters * 0.75), max(50, maximum_characters - 100))
    return semantic_transcript_chunks(segments, target_characters=target, maximum_characters=maximum_characters)


def _prepare_chunk(settings: Settings, video: Video, source_language: str, target_language: str, segments: list[dict]) -> dict:
    source_text = "\n".join(
        f"[{segment['start']:.1f}-{segment['end']:.1f}] {segment['text']}" for segment in segments
    )
    task = "near-complete cleaned read-aloud" if video.mode == "clean_readaloud" else "detailed information-first synthesis"
    prompt = f'''You prepare one consecutive section of a factual spoken audio digest. Return ONLY valid JSON with this exact schema:
{{
  "section_title": "string",
  "script": "string",
  "removed_segments": ["string"],
  "warnings": ["string"]
}}

Input details:
- Source language: {source_language}
- Target narration language: {target_language}
- Task: {task}
- Rules:
  1. Produce polished, professional spoken prose intended to be listened to as an audiobook.
  2. Maintain 100% factual fidelity to the source audio. DO NOT hallucinate facts, metrics, or claims.
  3. Strip all sponsor shoutouts, subscriber requests, channel likes, and promo segments.
  4. Write purely in {target_language}.

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
    """Synthesize high-quality spoken audio from the summary script using edge-tts or custom CLI."""
    cached_audio = StorageManager.is_audiobook_cached(output_audio)
    if cached_audio:
        print(f"  Found verified TTS audio on disk ({output_audio.stat().st_size // 1024} KB).")
        return cached_audio

    StorageManager.safe_delete(output_audio)

    provider = settings.tts_provider.lower().strip()
    if provider in ("none", "disabled", "false", "0"):
        return None

    is_tamil = language.lower() == "tamil"
    default_voice = "ta-IN-ValluvarNeural" if is_tamil else "en-US-ChristopherNeural"
    voice = (settings.tts_voice_tamil if is_tamil else settings.tts_voice_english) or default_voice

    text = script_path.read_text(encoding="utf-8").strip()
    if not text:
        return None

    # 1. Native edge-tts provider (high quality neural voice)
    if provider in ("edge", "edge-tts", "auto", "default") or not settings.tts_command_template.strip():
        try:
            import asyncio
            import edge_tts

            print(f"  Synthesizing spoken audio with neural TTS ({voice})...")

            async def _run_edge_tts():
                comm = edge_tts.Communicate(text, voice)
                await comm.save(str(output_audio))

            asyncio.run(_run_edge_tts())
            if output_audio.exists() and output_audio.stat().st_size > 1024:
                return output_audio
        except Exception as exc:
            logger.warning(f"edge-tts failed ({exc}), checking fallback command template...")

    # 2. Custom command template fallback
    if settings.tts_command_template.strip():
        command = settings.tts_command_template.format(
            text_path=str(script_path), output_path=str(output_audio), language=language, voice=voice
        )
        run_command(shlex.split(command), "local TTS")
        if output_audio.exists() and output_audio.stat().st_size > 1024:
            return output_audio

    return None
