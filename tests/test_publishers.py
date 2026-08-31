import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models import Settings, Video
from src.publishers import (
    RSSPodcastPublisher,
    YouTubeVideoPublisher,
    YTMusicLibraryPublisher,
    get_podcast_publisher,
)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            root=self.root,
            data_dir=self.root,
            completed_file=self.root / "completed.txt",
            podcast_publisher_mode="youtube_video",
        )
        self.video = Video(video_id="test_vid_123", title="Podcast Episode 1", url="https://youtube.com/watch?v=test_vid_123")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_podcast_publisher_factory(self):
        s_video = Settings(root=self.root, data_dir=self.root, completed_file=self.root / "c.txt", podcast_publisher_mode="youtube_video")
        p_video = get_podcast_publisher(s_video)
        self.assertIsInstance(p_video, YouTubeVideoPublisher)

        s_rss = Settings(root=self.root, data_dir=self.root, completed_file=self.root / "c.txt", podcast_publisher_mode="rss")
        p_rss = get_podcast_publisher(s_rss)
        self.assertIsInstance(p_rss, RSSPodcastPublisher)

        s_ytm = Settings(root=self.root, data_dir=self.root, completed_file=self.root / "c.txt", podcast_publisher_mode="ytmusic_library")
        p_ytm = get_podcast_publisher(s_ytm)
        self.assertIsInstance(p_ytm, YTMusicLibraryPublisher)

    @patch("src.publishers.youtube_video.subprocess.run")
    def test_youtube_video_publisher_render_and_local_fallback(self, mock_run):
        audio_file = self.root / "narration.mp3"
        audio_file.write_bytes(b"dummy mp3")
        thumb_file = self.root / "thumb.jpg"
        thumb_file.write_bytes(b"dummy thumb")

        pub = YouTubeVideoPublisher(self.settings)
        # Client secret does not exist -> renders video and returns RENDERED_LOCAL
        res = pub.publish_episode(
            video=self.video,
            audio_path=audio_file,
            summary_text="Summary of episode.",
            thumbnail_path=thumb_file,
            duration_seconds=180.0,
        )
        self.assertEqual(res["status"], "RENDERED_LOCAL")
        self.assertEqual(res["mode"], "youtube_video")
        self.assertIn("video_path", res)

    def test_rss_podcast_publisher(self):
        audio_file = self.root / "narration.mp3"
        audio_file.write_bytes(b"dummy mp3")

        rss_pub = RSSPodcastPublisher(self.settings)
        res = rss_pub.publish_episode(
            video=self.video,
            audio_path=audio_file,
            summary_text="Clean summary text for RSS podcast.",
            duration_seconds=120.0,
        )
        self.assertEqual(res["status"], "SUCCESS")
        rss_file = Path(res["rss_feed_path"])
        self.assertTrue(rss_file.exists())
        content = rss_file.read_text(encoding="utf-8")
        self.assertIn("Podcast Episode 1", content)
        self.assertIn("<enclosure", content)


if __name__ == "__main__":
    unittest.main()
