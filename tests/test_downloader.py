import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from src.downloader import download_audio, get_videos_from_url
from src.models import Video


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("subprocess.run")
    def test_get_videos_from_playlist(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({
            "entries": [
                {"id": "vid1", "title": "Video 1", "duration": 120, "uploader": "Chan 1"},
                {"id": "vid2", "title": "Video 2", "duration": 1500, "uploader": "Chan 2"},
            ]
        })
        mock_run.return_value = mock_proc

        videos = get_videos_from_url("https://www.youtube.com/playlist?list=PL123")
        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0].video_id, "vid1")
        self.assertEqual(videos[0].title, "Video 1")
        self.assertEqual(videos[0].mode, "clean_readaloud")
        self.assertEqual(videos[1].video_id, "vid2")
        self.assertEqual(videos[1].mode, "detailed_synthesis")

    @patch("subprocess.run")
    def test_get_single_video(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({
            "id": "single_vid",
            "title": "Single Video Title",
            "duration": 500,
            "uploader": "Cool Channel",
        })
        mock_run.return_value = mock_proc

        videos = get_videos_from_url("https://www.youtube.com/watch?v=single_vid")
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].video_id, "single_vid")
        self.assertEqual(videos[0].title, "Single Video Title")

    @patch("subprocess.run")
    def test_download_audio(self, mock_run):
        video = Video(video_id="v123", title="Test", url="https://youtu.be/v123")
        output_dir = self.root / "v123"

        def fake_run(args, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "source_audio.m4a").write_bytes(b"dummy audio")
            return MagicMock()

        mock_run.side_effect = fake_run

        audio_path = download_audio(video, output_dir)
        self.assertTrue(audio_path.exists())
        self.assertEqual(audio_path.name, "source_audio.m4a")

    def test_get_local_media_videos(self):
        from src.downloader import get_local_media_videos
        input_dir = self.root / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / "my_podcast.mp3").write_bytes(b"dummy")
        (input_dir / "sample_voice.wav").write_bytes(b"dummy")
        (input_dir / "notes.txt").write_text("not media")

        videos = get_local_media_videos(input_dir)
        self.assertEqual(len(videos), 2)
        titles = [v.title for v in videos]
        self.assertIn("my_podcast", titles)
        self.assertIn("sample_voice", titles)
        self.assertTrue(videos[0].video_id.startswith("local_"))


if __name__ == "__main__":
    unittest.main()
