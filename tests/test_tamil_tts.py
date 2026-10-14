"""
High-Quality Tamil & English TTS Comparison Test Script
Produces high-quality audio samples for user audition and comparison:
1. Pure English narration using Kokoro-82M (studio human cadence).
2. Pure Tamil narration using ta-IN-ValluvarNeural with prosody control.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import edge_tts




async def generate_tamil_sample(output_file: Path, text: str) -> Path | None:
    """Generates high-quality Tamil neural speech with optimized prosody."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    voice = "ta-IN-ValluvarNeural"
    try:
        communicate = edge_tts.Communicate(text, voice=voice, rate="-2%", pitch="+1Hz")
        await communicate.save(str(output_file))
        print(f"✅ High-quality Tamil sample saved to: {output_file} ({output_file.stat().st_size // 1024} KB)")
        return output_file
    except Exception as exc:
        print(f"⚠️ Tamil Edge-TTS service notice: {exc}")
        return None



async def generate_english_sample(output_file: Path, text: str) -> Path:
    """Generates high-quality English speech using Kokoro-82M (or edge-tts fallback)."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        from src.tts_engine import KokoroTTSEngine
        engine = KokoroTTSEngine()
        rendered = engine.synthesize(text, output_file, voice="af_heart", speed=1.0)
        if rendered and output_file.exists():
            print(f"✅ Kokoro-82M English sample saved to: {output_file} ({output_file.stat().st_size // 1024} KB)")
            return output_file
    except Exception as exc:
        print(f"⚠️ Kokoro synthesis fallback to Edge-TTS ({exc})...")

    voice = "en-US-ChristopherNeural"
    try:
        communicate = edge_tts.Communicate(text, voice=voice, rate="+0%", pitch="+0Hz")
        await communicate.save(str(output_file))
        print(f"✅ Neural English sample saved to: {output_file} ({output_file.stat().st_size // 1024} KB)")
        return output_file
    except Exception as exc:
        print(f"⚠️ English Edge-TTS service notice: {exc}")
        return None



def main():
    test_dir = Path("data") / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)

    tamil_sample_text = (
        "வணக்கம். இது ஒரு செயற்கை நுண்ணறிவு மூலம் உருவாக்கப்பட்ட உயர் தர ஆடியோ புத்தகம். "
        "நிகழ்படத்தின் மிக முக்கியமான கருத்துக்கள் மற்றும் நுணுக்கங்களை எந்தவித குழப்பமும் இன்றி "
        "நேரடியாகவும் துல்லியமாகவும் உங்களுக்கு வழங்குகிறது."
    )

    english_sample_text = (
        "Welcome to this high-density audio digest. Here is the single most important breakthrough: "
        "when you strip away the conversational padding and secondary anecdotes, the foundational mechanism "
        "relies on compounding consistency over raw sporadic motivation."
    )

    tamil_path = test_dir / "sample_tamil_valluvar.mp3"
    english_path = test_dir / "sample_english_audio.mp3"

    print("\n=======================================================")
    print("🎧 Generating Audition Samples (Tamil vs English TTS)")
    print("=======================================================")
    asyncio.run(generate_tamil_sample(tamil_path, tamil_sample_text))
    asyncio.run(generate_english_sample(english_path, english_sample_text))
    print("=======================================================\n")


if __name__ == "__main__":
    main()
