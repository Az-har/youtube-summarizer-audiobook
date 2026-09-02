from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from src.models import Settings, Video


class ProcessingError(RuntimeError):
    pass


def check_ollama_health(settings: Settings) -> bool:
    """Checks if the local Ollama server is responding."""
    try:
        req = urllib.request.Request(f"{settings.ollama_base_url}/api/tags", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
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
    import time
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
        print(f"  Warning: Failed to auto-start Ollama: {exc}")
        return False

    start_time = time.time()
    while time.time() - start_time < timeout:
        if check_ollama_health(settings):
            print("  Ollama server is ready!")
            return True
        time.sleep(1)

    return False


def _format_time(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"


def run_command(args: list[str], description: str) -> None:
    try:
        completed = subprocess.run(args, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise ProcessingError(f"{description} executable was not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        raise ProcessingError(f"{description} failed: {message[-1200:]}") from exc
    if completed.returncode:
        raise ProcessingError(f"{description} failed with exit code {completed.returncode}")




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
    except Exception:
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
        print(f"  Downloading AMD GPU Whisper model '{filename}' from Hugging Face...")
        urllib.request.urlretrieve(url, str(model_path))
        print(f"  Downloaded {filename} successfully.")
    return model_path




def _ollama(settings: Settings, prompt: str) -> dict:
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": True,
        "format": "json",
        "options": {
            "num_ctx": 4096,
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 40,
        }
    }
    request = urllib.request.Request(
        f"{settings.ollama_base_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    accumulated = []
    full_text = ""
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            for line in response:
                if not line:
                    continue
                try:
                    chunk_json = json.loads(line.decode("utf-8"))
                    token = chunk_json.get("response", "")
                    accumulated.append(token)
                    if len(accumulated) % 25 == 0:
                        print(".", end="", flush=True)
                except Exception:
                    continue
        print(" [OK]", flush=True)
        full_text = "".join(accumulated)
        return json.loads(full_text)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProcessingError(
            f"Could not connect to Ollama at {settings.ollama_base_url}. Please ensure Ollama is running: {exc}"
        ) from exc
    except (KeyError, json.JSONDecodeError) as exc:
        clean_sub = full_text.strip()
        if "{" in clean_sub and "}" in clean_sub:
            start_idx = clean_sub.find("{")
            end_idx = clean_sub.rfind("}") + 1
            try:
                return json.loads(clean_sub[start_idx:end_idx])
            except Exception:
                pass
        raise ProcessingError(f"Ollama did not return valid JSON. Response snippet: {full_text[:200]}") from exc


def _transcript_chunks(segments: list[dict], maximum_characters: int = 6000) -> list[list[dict]]:
    """Keep each local-LLM request within a fast, GPU-friendly chunk size (~3-4 minutes of speech)."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_size = 0
    for segment in segments:
        size = len(segment.get("text", "")) + 40
        if current and current_size + size > maximum_characters:
            chunks.append(current)
            current, current_size = [], 0
        current.append(segment)
        current_size += size
    if current:
        chunks.append(current)
    return chunks


def _prepare_chunk(settings: Settings, video: Video, source_language: str, target_language: str, segments: list[dict]) -> dict:
    source_text = "\n".join(
        f"[{segment['start']:.1f}-{segment['end']:.1f}] {segment['text']}" for segment in segments
    )
    task = "near-complete cleaned read-aloud" if video.mode == "clean_readaloud" else "detailed information-first synthesis"
    prompt = f'''You prepare one consecutive section of a factual spoken audio digest. Return ONLY valid JSON with this exact schema:
{{"script":"...","removed_segments":[{{"start":0,"end":0,"reason":"..."}}],"warnings":["..."]}}

Source language detected: {source_language}
Required output language: {target_language}
Output mode: {task}

Rules:
- Remove sponsor reads, advertisements, intros/outros, requests to like/subscribe/comment, self-promotion, repeated prompts, and unrelated banter.
- Keep all substantive claims, explanations, examples, qualifications, numbers, names, and conclusions.
- Do not invent facts. Put uncertain transcript portions in warnings.
- Translate only if the target language differs from the source language.
- Make the script natural for a single narrator, without mentioning these instructions.
- This is a sequential chunk. Do not add a global introduction or conclusion not present in this chunk.

Timestamped transcript:
{source_text}'''
    return _ollama(settings, prompt)


def _is_narration_complete(narration_data: dict, expected_chunks_count: int) -> bool:
    """Verifies that the narration script is non-empty and all expected chunks are present."""
    if not isinstance(narration_data, dict):
        return False
    script = narration_data.get("script", "")
    if not isinstance(script, str) or not script.strip():
        return False
    chunk_count = narration_data.get("chunk_count", 0)
    if chunk_count != expected_chunks_count:
        return False
    return True


def prepare_narration(settings: Settings, video: Video, transcript: dict, output_path: Path) -> dict:
    from src.evaluators import judge_summary, refine_summary_with_critique

    chunks = _transcript_chunks(transcript["segments"])
    if not chunks:
        raise ProcessingError("Whisper returned no speech segments")

    if output_path.exists():
        try:
            cached_data = json.loads(output_path.read_text(encoding="utf-8"))
            if _is_narration_complete(cached_data, len(chunks)):
                print(f"  Found verified complete summary on disk ({cached_data.get('chunk_count', len(chunks))} sections).")
                return cached_data
            else:
                print("  Existing cached summary is incomplete. Re-generating with Ollama...")
                try:
                    output_path.unlink()
                except Exception:
                    pass
        except Exception:
            print("  Cached summary is corrupt. Re-generating with Ollama...")
            try:
                output_path.unlink()
            except Exception:
                pass

    if not ensure_ollama_running(settings):
        raise ProcessingError(
            f"Ollama server is not reachable at {settings.ollama_base_url}. "
            "Please ensure Ollama is installed and running (e.g. start Ollama app or run 'ollama serve')."
        )
    target_language = "Tamil" if transcript.get("language", "").lower().startswith("ta") else "English"
    source_language = transcript.get("language", "unknown")
    
    print(f"  Summarizing & Self-Evaluating {len(chunks)} sections with {settings.ollama_model} ({target_language})...")
    prepared_chunks = []
    chunk_evaluations = []

    for idx, chunk in enumerate(chunks, start=1):
        start_ts = _format_time(chunk[0].get("start", 0))
        end_ts = _format_time(chunk[-1].get("end", 0))
        print(f"    [{idx}/{len(chunks)}] Section {start_ts} -> {end_ts} [Drafting", end="", flush=True)
        chunk_res = _prepare_chunk(settings, video, source_language, target_language, chunk)

        # Autonomous Critic Valuation Loop
        source_text = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in chunk)
        draft_script = chunk_res.get("script", "")
        eval_res = judge_summary(settings, source_text, draft_script, target_language, video.mode)

        if eval_res.status == "FAIL" and eval_res.issues:
            print(f" -> Critic Score: {eval_res.score}/10 (FAIL) -> Refining", end="", flush=True)
            refined_res = refine_summary_with_critique(
                settings, video, source_language, target_language, source_text, draft_script, eval_res.issues
            )
            if refined_res.get("script", "").strip():
                chunk_res = refined_res
                eval_res = judge_summary(settings, source_text, chunk_res["script"], target_language, video.mode)
                eval_res.retries_used = 1
                print(f" -> Refined Score: {eval_res.score}/10 ({eval_res.status})]", flush=True)
            else:
                print(f" -> Kept Draft ({eval_res.score}/10)]", flush=True)
        else:
            print(f" -> Critic Score: {eval_res.score}/10 ({eval_res.status})]", flush=True)

        prepared_chunks.append(chunk_res)
        chunk_evaluations.append(eval_res)

    if any(not isinstance(item.get("script"), str) or not item["script"].strip() for item in prepared_chunks):
        raise ProcessingError("Ollama returned no narration script for one or more transcript chunks")
    
    avg_critic_score = round(sum(e.score for e in chunk_evaluations) / len(chunk_evaluations), 2) if chunk_evaluations else 10.0
    prepared = {
        "target_language": target_language,
        "script": "\n\n".join(item["script"].strip() for item in prepared_chunks),
        "cleaned_source": "\n\n".join(item["script"].strip() for item in prepared_chunks),
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
    return prepared


def synthesize(settings: Settings, script_path: Path, output_audio: Path, language: str) -> Path | None:
    """Synthesize high-quality spoken audio from the summary script using edge-tts or custom CLI."""
    if output_audio.exists():
        if output_audio.stat().st_size > 1024:
            print(f"  Found verified TTS audio on disk ({output_audio.stat().st_size // 1024} KB).")
            return output_audio
        else:
            print("  Existing TTS audio is empty or corrupted. Re-synthesizing...")
            try:
                output_audio.unlink()
            except Exception:
                pass

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
                from src.evaluators import audit_audio
                audio_eval = audit_audio(output_audio, script_path, settings)
                print(f"  Audio Guard Verdict: {audio_eval.status} [Score: {audio_eval.score}/10] ({audio_eval.metrics.get('wpm', 0)} WPM, {audio_eval.metrics.get('file_size_kb', 0)} KB).")
                return output_audio
        except Exception as exc:
            print(f"  Warning: edge-tts failed ({exc}), checking fallback command template...")

    # 2. Custom command template fallback
    if settings.tts_command_template.strip():
        command = settings.tts_command_template.format(
            text_path=str(script_path), output_path=str(output_audio), language=language, voice=voice
        )
        run_command(shlex.split(command), "local TTS")
        if output_audio.exists() and output_audio.stat().st_size > 1024:
            from src.evaluators import audit_audio
            audio_eval = audit_audio(output_audio, script_path, settings)
            print(f"  Audio Guard Verdict: {audio_eval.status} [Score: {audio_eval.score}/10] ({audio_eval.metrics.get('wpm', 0)} WPM).")
            return output_audio

    return None

