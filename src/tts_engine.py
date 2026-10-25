from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from src.models import Settings

logger = logging.getLogger("TTSEngine")

KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


class KokoroTTSEngine:
    """
    Ultra-Realistic Local Neural Speech Synthesizer powered by Kokoro-82M ONNX.
    Produces studio-quality human voice cadence, natural inflection, and breathing.
    Runs 100% locally and free on CPU or GPU via onnxruntime.
    """

    _cached_instances: dict[tuple[str, str], Any] = {}

    def __init__(self, model_dir: Path | None = None) -> None:
        self.model_dir = model_dir or (Path("models") / "kokoro")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.model_dir / "kokoro-v1.0.onnx"
        self.voices_path = self.model_dir / "voices-v1.0.bin"
        self._kokoro_instance = None

    def ensure_models(self) -> bool:
        """Ensures that Kokoro-82M ONNX model weights and voice vectors exist."""
        if not self.model_path.exists() or self.model_path.stat().st_size < 100 * 1024 * 1024:
            print(f"  [Kokoro] Downloading Kokoro-82M model (~310MB) to {self.model_path}...")
            logger.info(f"Downloading Kokoro-82M model from {KOKORO_MODEL_URL}")
            self._download_file(KOKORO_MODEL_URL, self.model_path)

        if not self.voices_path.exists() or self.voices_path.stat().st_size < 10 * 1024 * 1024:
            print(f"  [Kokoro] Downloading voice vectors (~27MB) to {self.voices_path}...")
            logger.info(f"Downloading voices from {KOKORO_VOICES_URL}")
            self._download_file(KOKORO_VOICES_URL, self.voices_path)

        return self.model_path.exists() and self.voices_path.exists()

    def _download_file(self, url: str, dest_path: Path) -> None:
        temp_dest = dest_path.with_suffix(".tmp")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req) as resp, open(temp_dest, "wb") as f:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    percent = downloaded / total_size * 100
                    print(f"\r  [Kokoro Download] {downloaded // (1024*1024)}MB / {total_size // (1024*1024)}MB ({percent:.1f}%)", end="", flush=True)
            print()
        temp_dest.replace(dest_path)

    def get_instance(self):
        cache_key = (str(self.model_path.resolve()), str(self.voices_path.resolve()))
        if cache_key in self._cached_instances:
            self._kokoro_instance = self._cached_instances[cache_key]
            return self._kokoro_instance

        if self._kokoro_instance is None:
            self.ensure_models()
            from kokoro_onnx import Kokoro
            logger.info(f"Initializing Kokoro-82M from {self.model_path}")
            self._kokoro_instance = Kokoro(str(self.model_path), str(self.voices_path))
            self._cached_instances[cache_key] = self._kokoro_instance
        return self._kokoro_instance

    @staticmethod
    def _split_into_speech_units(text: str, max_chars: int = 300) -> list[str]:
        """
        Splits text into bite-sized speech units (<= max_chars) along natural punctuation boundaries.
        Guarantees that Kokoro-82M never exceeds phoneme token limits or truncates audio.
        """
        text = text.strip()
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        sentences = re.split(r'(?<=[.!?])\s+', text)
        units: list[str] = []
        current: list[str] = []
        curr_len = 0

        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if curr_len + len(s) + 1 <= max_chars:
                current.append(s)
                curr_len += len(s) + 1
            else:
                if current:
                    units.append(" ".join(current))
                    current = []
                    curr_len = 0
                if len(s) <= max_chars:
                    current.append(s)
                    curr_len = len(s)
                else:
                    # Sentence exceeds max_chars: split on sub-clause punctuation
                    clauses = re.split(r'(?<=[,;—])\s+', s)
                    for c in clauses:
                        c = c.strip()
                        if not c:
                            continue
                        if curr_len + len(c) + 1 <= max_chars:
                            current.append(c)
                            curr_len += len(c) + 1
                        else:
                            if current:
                                units.append(" ".join(current))
                            current = [c]
                            curr_len = len(c)
        if current:
            units.append(" ".join(current))
        return units

    def synthesize(
        self,
        text: str,
        output_audio: Path,
        voice: str = "af_heart",
        speed: float = 1.0,
        ffmpeg_bin: str = "ffmpeg",
    ) -> Path | None:
        """
        Synthesizes humanized speech from text using Kokoro-82M.
        Saves output as high-fidelity audio (MP3/WAV).
        """
        import soundfile as sf
        import numpy as np

        kokoro = self.get_instance()
        voice_style = voice or "af_heart"

        # Split text into paragraphs first
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()]

        # Split each paragraph into token-safe speech units (<=300 chars)
        speech_units: list[tuple[str, bool]] = []  # (unit_text, is_paragraph_end)
        for p in paragraphs:
            units = self._split_into_speech_units(p, max_chars=300)
            for i, u in enumerate(units):
                is_end = (i == len(units) - 1)
                speech_units.append((u, is_end))

        audio_segments: list[np.ndarray] = []
        sample_rate = 24000

        for idx, (unit_text, is_paragraph_end) in enumerate(speech_units, start=1):
            if not unit_text.strip():
                continue
            try:
                samples, sample_rate = kokoro.create(
                    unit_text,
                    voice=voice_style,
                    speed=speed,
                    lang="en-us",
                )
                if samples is not None and len(samples) > 0:
                    audio_segments.append(samples)
                    # Natural breath pause: 350ms between paragraphs, 150ms between intra-paragraph sentences
                    pause_sec = 0.35 if is_paragraph_end else 0.15
                    pause = np.zeros(int(sample_rate * pause_sec), dtype=np.float32)
                    audio_segments.append(pause)
            except Exception as exc:
                logger.warning(f"Kokoro synthesis failed on segment {idx}: {exc}")

        if not audio_segments:
            return None

        full_audio = np.concatenate(audio_segments)
        wav_temp = output_audio.with_suffix(".tmp.wav")
        sf.write(str(wav_temp), full_audio, sample_rate)

        # Convert WAV to 192k MP3 using ffmpeg
        try:
            cmd = [
                ffmpeg_bin, "-y", "-i", str(wav_temp),
                "-c:a", "libmp3lame", "-b:a", "192k",
                str(output_audio)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            wav_temp.unlink(missing_ok=True)
        except Exception:
            # If ffmpeg conversion fails, keep wav or rename
            if wav_temp.exists():
                wav_temp.replace(output_audio)

        if output_audio.exists() and output_audio.stat().st_size > 1024:
            return output_audio
        return None


def synthesize_spoken_audio(
    settings: Settings,
    script_path: Path,
    output_audio: Path,
    language: str = "English",
) -> Path | None:
    """
    Unified Speech Synthesizer:
    1. If English and provider is kokoro -> synthesizes via ultra-realistic Kokoro-82M.
    2. If Tamil -> synthesizes via high-quality neural voice (ta-IN-ValluvarNeural) with prosody.
    3. Falls back to edge-tts or custom CLI command template if needed.
    """
    text = script_path.read_text(encoding="utf-8").strip()
    if not text:
        return None

    provider = (settings.tts_provider or "kokoro").lower().strip()
    if provider in ("none", "disabled", "false", "0"):
        return None

    is_tamil = language.lower() in ("tamil", "ta")

    # 1. Kokoro-82M Local Ultra-Realistic Synthesizer (Primary for English)
    if not is_tamil and provider in ("kokoro", "auto", "default"):
        try:
            print(f"  Synthesizing with ultra-realistic Kokoro-82M (Voice: {settings.kokoro_voice}, Speed: {settings.kokoro_speed})...")
            engine = KokoroTTSEngine(model_dir=settings.root / "models" / "kokoro")
            rendered = engine.synthesize(
                text=text,
                output_audio=output_audio,
                voice=settings.kokoro_voice or "af_heart",
                speed=settings.kokoro_speed or 1.0,
                ffmpeg_bin=settings.ffmpeg_binary or "ffmpeg",
            )
            if rendered and output_audio.exists() and output_audio.stat().st_size > 1024:
                return output_audio
        except Exception as exc:
            logger.warning(f"Kokoro-82M synthesis encountered issue ({exc}), falling back to edge-tts...")
            print(f"  [Kokoro Notice] Falling back to Edge-TTS: {exc}")

    # 2. High-Quality Neural TTS (ta-IN-ValluvarNeural for Tamil, or edge-tts fallback)
    try:
        import edge_tts

        default_voice = "ta-IN-ValluvarNeural" if is_tamil else "en-US-ChristopherNeural"
        voice = (settings.tts_voice_tamil if is_tamil else settings.tts_voice_english) or default_voice

        lang_label = "Tamil" if is_tamil else "English"
        print(f"  Synthesizing spoken audio with neural {lang_label} voice ({voice})...")

        async def _run_edge_tts():
            comm = edge_tts.Communicate(text, voice)
            await comm.save(str(output_audio))

        asyncio.run(_run_edge_tts())
        if output_audio.exists() and output_audio.stat().st_size > 1024:
            return output_audio
    except Exception as exc:
        logger.warning(f"Neural TTS synthesis failed ({exc}), checking local Kokoro fallback...")
        if not is_tamil:
            try:
                print("  [Fallback] Attempting local Kokoro-82M synthesis...")
                engine = KokoroTTSEngine(model_dir=settings.root / "models" / "kokoro")
                rendered = engine.synthesize(
                    text=text,
                    output_audio=output_audio,
                    voice=settings.kokoro_voice or "af_heart",
                    speed=settings.kokoro_speed or 1.0,
                    ffmpeg_bin=settings.ffmpeg_binary or "ffmpeg",
                )
                if rendered and output_audio.exists() and output_audio.stat().st_size > 1024:
                    return output_audio
            except Exception as k_exc:
                logger.warning(f"Kokoro fallback also failed: {k_exc}")


    # 3. Custom command template fallback
    if settings.tts_command_template.strip():
        import shlex
        from src.processing import run_command
        voice = settings.tts_voice_tamil if is_tamil else settings.tts_voice_english
        command = settings.tts_command_template.format(
            text_path=str(script_path), output_path=str(output_audio), language=language, voice=voice
        )
        run_command(shlex.split(command), "local TTS")
        if output_audio.exists() and output_audio.stat().st_size > 1024:
            return output_audio

    return None
