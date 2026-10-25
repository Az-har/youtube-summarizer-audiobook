from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models import Settings
from src.tts_engine import KokoroTTSEngine, synthesize_spoken_audio


class TestKokoroTTS(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.script_file = self.root / "summary.txt"
        self.script_file.write_text("This is an ultra-realistic audiobook test narration.", encoding="utf-8")
        self.output_audio = self.root / "narration.mp3"

        self.settings = Settings(
            root=self.root,
            data_dir=self.root / "data",
            completed_file=self.root / "data" / "completed.txt",
            tts_provider="kokoro",
            kokoro_voice="af_heart",
            kokoro_speed=1.0,
            tts_voice_tamil="ta-IN-ValluvarNeural",
            tts_voice_english="en-US-ChristopherNeural",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_kokoro_engine_init(self):
        engine = KokoroTTSEngine(model_dir=self.root / "models" / "kokoro")
        self.assertEqual(engine.model_path.name, "kokoro-v1.0.onnx")
        self.assertEqual(engine.voices_path.name, "voices-v1.0.bin")

    def test_disabled_tts_provider(self):
        disabled_settings = Settings(
            root=self.root,
            data_dir=self.root / "data",
            completed_file=self.root / "data" / "completed.txt",
            tts_provider="none",
        )
        res = synthesize_spoken_audio(disabled_settings, self.script_file, self.output_audio, "English")
        self.assertIsNone(res)

    @patch("src.tts_engine.KokoroTTSEngine.synthesize")
    def test_english_routes_to_kokoro(self, mock_synth):
        mock_synth.return_value = self.output_audio
        # Create dummy file to simulate success
        self.output_audio.write_bytes(b"dummy audio" * 200)

        res = synthesize_spoken_audio(self.settings, self.script_file, self.output_audio, "English")
        self.assertEqual(res, self.output_audio)
        mock_synth.assert_called_once()

    @patch("edge_tts.Communicate.save")
    def test_tamil_routes_to_neural_tts(self, mock_save):
        async def fake_save(path):
            Path(path).write_bytes(b"dummy tamil audio" * 200)

        mock_save.side_effect = fake_save

        res = synthesize_spoken_audio(self.settings, self.script_file, self.output_audio, "Tamil")
        self.assertEqual(res, self.output_audio)

    def test_split_into_speech_units_bounds(self):
        long_paragraph = (
            "This is a long introductory sentence detailing the foundation of computer science and algorithm design. "
            "Artificial intelligence models require extensive linear algebra, calculus, and matrix optimizations to function at scale. "
            "When deploying neural networks in local environments, memory management and inference scheduling become critical bottlenecks. "
            "Therefore, optimizing model quantization, attention mechanisms, and pipeline throughput guarantees optimal real-time performance."
        )
        units = KokoroTTSEngine._split_into_speech_units(long_paragraph, max_chars=180)
        self.assertGreater(len(units), 1)
        for u in units:
            self.assertLessEqual(len(u), 180)
            self.assertTrue(len(u.strip()) > 0)

    @patch("kokoro_onnx.Kokoro")
    @patch("src.tts_engine.KokoroTTSEngine.ensure_models")
    def test_kokoro_shared_instance_cache(self, mock_ensure, mock_kokoro_cls):
        mock_instance = MagicMock()
        mock_kokoro_cls.return_value = mock_instance

        engine1 = KokoroTTSEngine(model_dir=self.root / "models" / "kokoro")
        engine2 = KokoroTTSEngine(model_dir=self.root / "models" / "kokoro")

        inst1 = engine1.get_instance()
        inst2 = engine2.get_instance()

        self.assertIs(inst1, inst2)
        mock_kokoro_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
