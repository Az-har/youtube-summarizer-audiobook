import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.evaluators.audio_evaluator import audit_audio
from src.evaluators.summary_evaluator import judge_summary, refine_summary_with_critique
from src.evaluators.transcript_evaluator import (
    _calculate_ngram_repetition_rate,
    audit_transcript,
)
from src.models import QualityScorecard, Settings, Video


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            root=self.root,
            data_dir=self.root,
            completed_file=self.root / "completed.txt",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ngram_repetition_rate(self):
        repetitive = "thank you for watching this thank you for watching this thank you for watching this"
        rate = _calculate_ngram_repetition_rate(repetitive, n=4)
        self.assertGreater(rate, 0.5)

        normal = "the quick brown fox jumps over the lazy dog and runs into the forest"
        rate_normal = _calculate_ngram_repetition_rate(normal, n=4)
        self.assertEqual(rate_normal, 0.0)

    def test_audit_transcript_healthy(self):
        transcript = {
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "Hello world welcome to this episode."},
                {"start": 5.0, "end": 10.0, "text": "We are discussing technology and artificial intelligence today."},
            ]
        }
        res = audit_transcript(transcript, audio_duration=10.0)
        self.assertEqual(res.status, "PASS")
        self.assertGreaterEqual(res.score, 8.0)

    def test_audit_transcript_incomplete(self):
        transcript = {
            "segments": [
                {"start": 0.0, "end": 10.0, "text": "Short snippet only."},
            ]
        }
        res = audit_transcript(transcript, audio_duration=100.0)
        self.assertIn("Incomplete audio coverage", res.issues[0])
        self.assertLess(res.score, 8.0)

    def test_judge_summary_heuristic_sponsor_detection(self):
        # When Ollama critic is not called/mocked, heuristic fallback flags sponsors
        with patch("src.evaluators.summary_evaluator._call_critic_ollama", return_value={}):
            draft = "This is a great video. Don't forget to like and subscribe to my channel."
            res = judge_summary(
                self.settings,
                source_chunk_text="Some source text",
                draft_script=draft,
                target_language="English",
                mode="clean_readaloud",
            )
            self.assertTrue(any("sponsor" in issue.lower() for issue in res.issues))

    def test_judge_summary_llm_critic(self):
        mock_critic_res = {
            "faithfulness_score": 9.5,
            "ad_removal_score": 9.0,
            "spoken_flow_score": 9.0,
            "language_fidelity_score": 9.5,
            "overall_score": 9.3,
            "issues": [],
            "verdict": "PASS",
        }
        with patch("src.evaluators.summary_evaluator._call_critic_ollama", return_value=mock_critic_res):
            res = judge_summary(
                self.settings,
                source_chunk_text="Source speech",
                draft_script="Draft summary",
                target_language="English",
                mode="clean_readaloud",
            )
            self.assertEqual(res.status, "PASS")
            self.assertGreaterEqual(res.score, 9.0)

    @patch("src.processing._get_audio_duration", return_value=60.0)
    @patch("src.processing._find_ffmpeg", return_value="ffmpeg")
    @patch("subprocess.run")
    def test_audit_audio_healthy(self, mock_run, mock_find, mock_dur):
        mock_run.return_value = MagicMock(stderr="max_volume: -5.2 dB", returncode=0)
        audio_file = self.root / "audio.mp3"
        audio_file.write_bytes(b"dummy audio" * 200)
        script_file = self.root / "script.txt"
        # 140 words in 60s = 140 WPM (perfect normal reading pacing)
        script_file.write_text("word " * 140, encoding="utf-8")

        res = audit_audio(audio_file, script_file, self.settings)
        self.assertEqual(res.status, "PASS")
        self.assertGreaterEqual(res.score, 8.0)

    def test_quality_scorecard_aggregation(self):
        scorecard = QualityScorecard(video_id="test_vid")
        from src.models import EvaluationResult

        r1 = EvaluationResult(stage="transcription", status="PASS", score=9.0)
        r2 = EvaluationResult(stage="summarization", status="PASS", score=9.5)
        scorecard.add_result(r1)
        scorecard.add_result(r2)

        self.assertEqual(scorecard.overall_status, "PASS")
        self.assertEqual(scorecard.overall_score, 9.25)
        d = scorecard.to_dict()
        self.assertEqual(d["video_id"], "test_vid")
        self.assertIn("transcription", d["stages"])


if __name__ == "__main__":
    unittest.main()
