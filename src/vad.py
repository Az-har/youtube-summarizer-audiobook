from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("VAD")


def _get_silero_vad_model(models_dir: Path) -> Path | None:
    """Ensures the lightweight Silero VAD ONNX model is available locally."""
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / "silero_vad.onnx"
    if not model_path.exists() or model_path.stat().st_size == 0:
        url = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
        try:
            logger.info("Downloading Silero VAD neural ONNX model (~2MB)...")
            urllib.request.urlretrieve(url, str(model_path))
        except Exception as exc:
            logger.warning(f"Could not download Silero VAD model: {exc}")
            return None
    return model_path


def apply_silero_vad(
    wav_16k_path: Path,
    models_dir: Path,
    threshold: float = 0.5,
    min_speech_duration_ms: int = 250,
    min_silence_duration_ms: int = 150,
) -> list[dict[str, float]]:
    """
    Applies neural Silero VAD to identify speech intervals.
    Returns list of speech intervals: [{"start": 0.5, "end": 4.2}, ...]
    """
    try:
        import numpy as np
        import onnxruntime as ort
        import wave

        model_path = _get_silero_vad_model(models_dir)
        if not model_path or not model_path.exists():
            return []

        # Read 16kHz WAV file
        with wave.open(str(wav_16k_path), "rb") as wf:
            n_channels = wf.getnchannels()
            sample_rate = wf.getframerate()
            if sample_rate != 16000 or n_channels != 1:
                return []
            pcm_data = wf.readframes(wf.getnframes())
            audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0

        # Run ONNX inference
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        window_size_samples = 512  # 32ms at 16kHz
        h = np.zeros((2, 1, 64), dtype=np.float32)
        c = np.zeros((2, 1, 64), dtype=np.float32)

        speech_segments: list[dict[str, float]] = []
        is_speech = False
        speech_start = 0.0

        for i in range(0, len(audio) - window_size_samples, window_size_samples):
            chunk = audio[i : i + window_size_samples][np.newaxis, :]
            sr_tensor = np.array(16000, dtype=np.int64)

            ort_inputs = {
                "input": chunk,
                "sr": sr_tensor,
                "h": h,
                "c": c,
            }
            ort_outs = session.run(None, ort_inputs)
            out_prob = float(ort_outs[0][0][0])
            h, c = ort_outs[1], ort_outs[2]

            current_time = i / 16000.0

            if out_prob >= threshold and not is_speech:
                is_speech = True
                speech_start = current_time
            elif out_prob < (threshold - 0.15) and is_speech:
                is_speech = False
                speech_end = current_time
                if (speech_end - speech_start) * 1000.0 >= min_speech_duration_ms:
                    speech_segments.append({"start": round(speech_start, 2), "end": round(speech_end, 2)})

        if is_speech:
            speech_segments.append({"start": round(speech_start, 2), "end": round(len(audio) / 16000.0, 2)})

        return speech_segments

    except Exception as exc:
        logger.warning(f"Silero VAD processing skipped: {exc}")
        return []
