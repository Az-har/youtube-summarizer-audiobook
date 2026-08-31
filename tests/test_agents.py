import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.agents.audiobook import AudiobookAgent
from src.agents.base import AgentContext
from src.agents.editorial import EditorialAgent
from src.agents.ingestion import IngestionAgent
from src.agents.supervisor import SupervisorAgent, process_video_agentic
from src.agents.transcription import TranscriptionAgent
from src.models import EvaluationResult, Settings, Video


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            root=self.root,
            data_dir=self.root,
            completed_file=self.root / "completed.txt",
            tts_provider="edge",
        )
        self.video = Video(video_id="v_agent_test", title="Agentic Testing", url="https://example.com/v1")
        self.working_dir = self.root / "videos" / self.video.video_id
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.context = AgentContext(settings=self.settings, video=self.video, working_dir=self.working_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("src.agents.ingestion.download_audio")
    @patch("src.agents.ingestion._get_audio_duration", return_value=120.0)
    @patch("src.agents.ingestion.subprocess.run")
    def test_ingestion_agent(self, mock_sub, mock_dur, mock_down):
        fake_audio = self.working_dir / "source.m4a"
        fake_audio.write_bytes(b"dummy")
        mock_down.return_value = fake_audio

        agent = IngestionAgent()
        res = agent.run(self.context)
        self.assertEqual(res["duration_seconds"], 120.0)
        self.assertIn("wav_16k_path", res)

    @patch("src.agents.editorial._is_narration_complete", return_value=False)
    @patch("src.agents.editorial.ensure_ollama_running", return_value=True)
    @patch("src.agents.editorial._prepare_chunk")
    @patch("src.agents.editorial.judge_summary")
    def test_editorial_agent(self, mock_judge, mock_prep, mock_ollama, mock_comp):
        mock_prep.return_value = {"script": "Fact checked narrative.", "removed_segments": [], "warnings": []}
        mock_judge.return_value = EvaluationResult(stage="summarization", status="PASS", score=9.2)

        self.context.state["transcript_data"] = {
            "language": "en",
            "segments": [{"start": 0, "end": 10, "text": "Speech content here."}],
        }
        agent = EditorialAgent()
        res = agent.run(self.context)
        self.assertEqual(res["target_language"], "English")
        self.assertIn("Fact checked narrative", res["script"])
        self.assertEqual(res["critic_score"], 9.2)

    @patch("src.agents.audiobook.synthesize")
    @patch("src.agents.audiobook.audit_audio")
    @patch("src.agents.audiobook.subprocess.run")
    def test_audiobook_agent(self, mock_sub, mock_audit, mock_synth):
        out_mp3 = self.working_dir / "narration.mp3"
        out_mp3.write_bytes(b"mp3 content" * 200)
        mock_synth.return_value = out_mp3
        mock_audit.return_value = EvaluationResult(stage="tts_audio", status="PASS", score=9.8, metrics={"wpm": 140})

        (self.working_dir / "summary.txt").write_text("Hello world", encoding="utf-8")
        agent = AudiobookAgent()
        res = agent.run(self.context)
        self.assertEqual(res["audio_guard_score"], 9.8)


if __name__ == "__main__":
    unittest.main()
