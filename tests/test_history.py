import tempfile
import unittest
from pathlib import Path

from src.history import append_completed, completed_ids


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.history_file = self.root / "completed_videos.txt"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_completed_ids_empty(self):
        self.assertEqual(completed_ids(self.history_file), set())

    def test_completed_ids_reads_correctly(self):
        self.history_file.write_text(
            "# comment\n"
            "vid1\n"
            "vid2, optional title or details\n"
            "\n",
            encoding="utf-8",
        )
        self.assertEqual(completed_ids(self.history_file), {"vid1", "vid2"})

    def test_append_completed(self):
        append_completed(self.history_file, "vid_abc")
        self.assertEqual(completed_ids(self.history_file), {"vid_abc"})
        append_completed(self.history_file, "vid_xyz")
        self.assertEqual(completed_ids(self.history_file), {"vid_abc", "vid_xyz"})


if __name__ == "__main__":
    unittest.main()
