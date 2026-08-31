import tempfile
import unittest
from pathlib import Path

from src.daemon.queue import TaskQueue
from src.daemon.watcher import IngestionWatcher


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.state_file = self.root / "state.json"
        self.queue = TaskQueue(self.state_file)
        self.input_dir = self.root / "input"
        self.playlist_file = self.root / "playlists.txt"
        self.watcher = IngestionWatcher(self.queue, self.input_dir, self.playlist_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_task_queue_lifecycle(self):
        task = self.queue.enqueue("https://youtube.com/watch?v=123", "youtube")
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "QUEUED")

        # Duplicate enqueue returns None
        dup = self.queue.enqueue("https://youtube.com/watch?v=123", "youtube")
        self.assertIsNone(dup)

        next_task = self.queue.get_next_queued()
        self.assertIsNotNone(next_task)
        self.assertEqual(next_task.task_id, task.task_id)

        self.queue.update_status(task.task_id, "PROCESSING")
        tasks = self.queue.list_tasks()
        self.assertEqual(tasks[0].status, "PROCESSING")

        self.queue.update_status(task.task_id, "COMPLETED")
        tasks = self.queue.list_tasks()
        self.assertEqual(tasks[0].status, "COMPLETED")

    def test_watcher_scan_local_and_playlist(self):
        # Create a local audio file in input/
        (self.input_dir / "my_podcast.mp3").write_bytes(b"dummy")
        (self.input_dir / "notes.txt").write_text("not media")

        # Create a playlist file
        self.playlist_file.write_text(
            "# Comment line\nhttps://www.youtube.com/playlist?list=PLTest123\n\n",
            encoding="utf-8"
        )

        enqueued = self.watcher.scan_and_enqueue()
        self.assertEqual(enqueued, 2)

        tasks = self.queue.list_tasks()
        self.assertEqual(len(tasks), 2)
        types = [t.source_type for t in tasks]
        self.assertIn("local_file", types)
        self.assertIn("youtube", types)


if __name__ == "__main__":
    unittest.main()
