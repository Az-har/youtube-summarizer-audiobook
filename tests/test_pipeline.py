from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.history import completed_ids
from src.models import Settings, Video
from src.pipeline import process_url, process_video


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.completed_file = self.root / "completed.txt"
        self.data_dir = self.root / "data"
        self.settings = Settings(
            root=self.root,
            data_dir=self.data_dir,
            completed_file=self.completed_file,
            ollama_base_url="http://localhost:11434",
            ollama_model="qwen3:14b",
            whisper_model="large-v3",
            whisper_device="auto",
            whisper_compute_type="int8",
            tts_provider="command",
            tts_command_template="",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("src.pipeline.get_videos_from_url")
    def test_process_url_dry_run(self, mock_get_videos):
        v1 = Video(video_id="v1", title="Title 1", url="https://v1", duration_seconds=100)
        mock_get_videos.return_value = [v1]

        results = process_url(self.settings, "https://playlist", dry_run=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "dry_run")
        self.assertEqual(completed_ids(self.completed_file), set())

    @patch("src.pipeline.get_videos_from_url")
    @patch("src.pipeline.process_video")
    def test_process_url_live(self, mock_process_vid, mock_get_videos):
        v1 = Video(video_id="v1", title="Title 1", url="https://v1", duration_seconds=100)
        v2 = Video(video_id="v2", title="Title 2", url="https://v2", duration_seconds=200)
        mock_get_videos.return_value = [v1, v2]

        from src.models import ProcessResult
        mock_process_vid.side_effect = [
            ProcessResult(video=v1, status="completed", summary_path="path1"),
            ProcessResult(video=v2, status="completed", summary_path="path2"),
        ]

        results = process_url(self.settings, "https://playlist", dry_run=False)
        self.assertEqual(len(results), 2)
        self.assertEqual(completed_ids(self.completed_file), {"v1", "v2"})


if __name__ == "__main__":
    unittest.main()
