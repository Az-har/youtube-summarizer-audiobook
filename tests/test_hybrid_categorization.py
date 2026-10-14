from __future__ import annotations

import unittest
from src.models import Video


class TestHybridCategorization(unittest.TestCase):
    def test_explicit_translate_mode(self):
        v = Video(
            video_id="vid_1",
            title="Tamil Cooking Tutorial",
            url="https://youtube.com/watch?v=1",
            requested_mode="translate",
        )
        self.assertEqual(v.mode, "translation")

    def test_explicit_summarize_mode(self):
        v = Video(
            video_id="vid_2",
            title="3 Hour Machine Learning Masterclass",
            url="https://youtube.com/watch?v=2",
            requested_mode="summarize",
        )
        self.assertEqual(v.mode, "absolute_summary")

    def test_auto_fallback_short_video(self):
        v = Video(
            video_id="vid_3",
            title="Quick Tip",
            url="https://youtube.com/watch?v=3",
            duration_seconds=500,  # < 20 mins
            requested_mode="auto",
        )
        self.assertEqual(v.mode, "clean_readaloud")

    def test_auto_fallback_long_video(self):
        v = Video(
            video_id="vid_4",
            title="Deep Dive Discussion",
            url="https://youtube.com/watch?v=4",
            duration_seconds=3600,  # 60 mins
            requested_mode="auto",
        )
        self.assertEqual(v.mode, "detailed_synthesis")


if __name__ == "__main__":
    unittest.main()
