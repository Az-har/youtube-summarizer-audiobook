import os
import tempfile
import unittest
from pathlib import Path

from src.config import load_env, load_playlist_urls, load_settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_env(self):
        env_file = self.root / ".env"
        env_file.write_text("TEST_KEY_1=hello\nTEST_KEY_2=\"world\"\n# comment\n", encoding="utf-8")
        load_env(env_file)
        self.assertEqual(os.environ.get("TEST_KEY_1"), "hello")
        self.assertEqual(os.environ.get("TEST_KEY_2"), "world")

    def test_load_settings_defaults(self):
        settings = load_settings(self.root)
        self.assertEqual(settings.whisper_model, "large-v3")
        self.assertEqual(settings.ollama_model, "qwen3:14b")
        self.assertEqual(settings.tts_provider, "command")

    def test_load_playlist_urls(self):
        urls_file = self.root / "playlists.txt"
        urls_file.write_text(
            "# Playlists to process\n"
            "https://www.youtube.com/playlist?list=PL123\n"
            "\n"
            "https://www.youtube.com/watch?v=vid1\n",
            encoding="utf-8",
        )
        urls = load_playlist_urls(urls_file)
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0], "https://www.youtube.com/playlist?list=PL123")
        self.assertEqual(urls[1], "https://www.youtube.com/watch?v=vid1")


if __name__ == "__main__":
    unittest.main()
