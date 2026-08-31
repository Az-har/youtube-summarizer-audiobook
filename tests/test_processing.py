from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.models import Settings, Video
from src.processing import (
    _transcript_chunks,
    prepare_narration,
    synthesize,
)


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            root=self.root,
            data_dir=self.root,
            completed_file=self.root / "completed.txt",
            ollama_base_url="http://localhost:11434",
            ollama_model="qwen3:14b",
            whisper_model="large-v3",
            whisper_device="auto",
            whisper_compute_type="int8",
            tts_provider="command",
            tts_command_template="tts-cli --input {text_path} --output {output_path} --lang {language} --voice {voice}",
            tts_voice_tamil="ta_v1",
            tts_voice_english="en_v1",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_video_mode_duration_rule(self):
        short_vid = Video("short", "Short", "https://url", duration_seconds=19 * 60 + 59)
        self.assertEqual(short_vid.mode, "clean_readaloud")

        long_vid = Video("long", "Long", "https://url", duration_seconds=20 * 60)
        self.assertEqual(long_vid.mode, "detailed_synthesis")

    def test_transcript_chunks_splitting(self):
        segments = [{"start": i, "end": i + 1, "text": "A" * 100} for i in range(10)]
        chunks = _transcript_chunks(segments, maximum_characters=300)
        self.assertTrue(len(chunks) > 1)
        self.assertEqual(sum(len(c) for c in chunks), 10)

    @patch("src.processing.ensure_ollama_running", return_value=True)
    @patch("src.processing._ollama")
    def test_prepare_narration_tamil(self, mock_ollama, mock_ensure):
        mock_ollama.return_value = {
            "script": "தமிழ் உரை",
            "removed_segments": [],
            "warnings": [],
        }
        vid = Video("ta_vid", "Tamil Title", "https://url", duration_seconds=300)
        transcript = {
            "language": "ta",
            "segments": [{"start": 0, "end": 5, "text": "வணக்கம்"}],
        }
        out_path = self.root / "narration.json"
        res = prepare_narration(self.settings, vid, transcript, out_path)
        self.assertEqual(res["target_language"], "Tamil")
        self.assertIn("தமிழ்", res["script"])

    @patch("src.processing.ensure_ollama_running", return_value=True)
    @patch("src.processing._ollama")
    def test_prepare_narration_english_or_translation(self, mock_ollama, mock_ensure):
        mock_ollama.return_value = {
            "script": "English summary.",
            "removed_segments": [{"start": 0, "end": 5, "reason": "sponsor"}],
            "warnings": [],
        }
        vid = Video("fr_vid", "French Video", "https://url", duration_seconds=300)
        transcript = {
            "language": "fr",
            "segments": [{"start": 0, "end": 5, "text": "Bonjour"}],
        }
        out_path = self.root / "narration_fr.json"
        res = prepare_narration(self.settings, vid, transcript, out_path)
        self.assertEqual(res["target_language"], "English")

    @patch("src.processing.run_command")
    def test_synthesize_tts_command(self, mock_run):
        script_file = self.root / "script.txt"
        script_file.write_text("Test", encoding="utf-8")
        out_audio = self.root / "narration.mp3"

        def fake_run(args, desc):
            out_audio.write_bytes(b"dummy audio content" * 100)

        mock_run.side_effect = fake_run

        res = synthesize(self.settings, script_file, out_audio, "Tamil")
        self.assertIsNotNone(res)
        call_args = mock_run.call_args[0][0]
        self.assertIn("ta_v1", call_args)
        self.assertIn("Tamil", call_args)

    @patch("src.processing.ensure_ollama_running", return_value=False)
    def test_prepare_narration_ollama_down(self, mock_ensure):
        from src.processing import ProcessingError
        vid = Video("v1", "Test", "https://url", duration_seconds=100)
        transcript = {"language": "en", "segments": [{"start": 0, "end": 1, "text": "Hi"}]}
        with self.assertRaises(ProcessingError) as ctx:
            prepare_narration(self.settings, vid, transcript, self.root / "n.json")
        self.assertIn("Ollama server is not reachable", str(ctx.exception))

    @patch("src.processing._get_audio_duration", return_value=120.0)
    def test_is_transcript_complete(self, mock_dur):
        from src.processing import _is_transcript_complete
        # Incomplete (only 30s covered of 120s)
        incomplete = {"segments": [{"start": 0, "end": 30, "text": "Short"}]}
        self.assertFalse(_is_transcript_complete(incomplete, Path("fake.m4a"), self.settings))

        # Complete (115s covered of 120s)
        complete = {"segments": [{"start": 0, "end": 115, "text": "Full"}]}
        self.assertTrue(_is_transcript_complete(complete, Path("fake.m4a"), self.settings))

        # Corrupt / Empty
        self.assertFalse(_is_transcript_complete({}, Path("fake.m4a"), self.settings))

    def test_is_narration_complete(self):
        from src.processing import _is_narration_complete
        valid = {"script": "A factual summary.", "chunk_count": 2}
        self.assertTrue(_is_narration_complete(valid, 2))

        # Mismatched chunk count
        self.assertFalse(_is_narration_complete(valid, 3))

        # Empty script
        empty = {"script": "", "chunk_count": 2}
        self.assertFalse(_is_narration_complete(empty, 2))


if __name__ == "__main__":
    unittest.main()
