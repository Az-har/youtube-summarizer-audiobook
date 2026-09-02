from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

from src.agents import process_video_agentic
from src.daemon.queue import Task, TaskQueue
from src.daemon.watcher import IngestionWatcher
from src.downloader import get_videos_from_url
from src.models import Settings, Video


class DaemonService:
    """
    Background Daemon Service: Runs silently in the background, continuously monitoring
    data/input/ and playlists.txt, coordinating the specialized AI agents.
    """

    def __init__(self, settings: Settings, poll_interval: int = 5) -> None:
        self.settings = settings
        self.poll_interval = poll_interval
        self.running = False

        # Setup logging to data/logs/daemon.log
        log_dir = settings.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "daemon.log"

        logging.basicConfig(
            filename=str(log_file),
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            encoding="utf-8",
        )
        self.logger = logging.getLogger("Daemon")

        # Initialize Queue and Watcher
        state_file = settings.data_dir / "state.json"
        self.queue = TaskQueue(state_file)
        self.watcher = IngestionWatcher(
            queue=self.queue,
            input_dir=settings.data_dir / "input",
            playlist_file=settings.root / "playlists.txt",
        )

    def start(self) -> None:
        """Starts the background worker loop."""
        self.running = True
        print(f"\n=======================================================")
        print(f"🤖 Autonomous AI Daemon Service Started!")
        print(f"   Watching directory: {self.settings.data_dir / 'input'}")
        print(f"   Watching playlists: {self.settings.root / 'playlists.txt'}")
        print(f"   Log file: {self.settings.data_dir / 'logs' / 'daemon.log'}")
        print(f"   Press Ctrl+C to stop.")
        print(f"=======================================================\n")
        self.logger.info("Daemon service started.")

        def _handle_exit(sig, frame):
            print("\n[Daemon] Stopping background worker gracefully...")
            self.running = False

        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)

        while self.running:
            try:
                # 1. Check for newly dropped media or playlist entries
                self.watcher.scan_and_enqueue()

                # 2. Process next queued task
                task = self.queue.get_next_queued()
                if task:
                    self._process_task(task)
                else:
                    # Idle sleep
                    time.sleep(self.poll_interval)

            except Exception as exc:
                self.logger.error(f"Unexpected daemon error: {exc}", exc_info=True)
                time.sleep(self.poll_interval)

        self.logger.info("Daemon service stopped.")
        print("[Daemon] Service stopped cleanly.")

    def _process_task(self, task: Task) -> None:
        self.logger.info(f"Processing Task: {task.task_id} ({task.source_type}: {task.target})")
        self.queue.update_status(task.task_id, "PROCESSING")

        try:
            if task.source_type == "local_file":
                file_path = Path(task.target)
                if not file_path.exists():
                    self.queue.update_status(task.task_id, "FAILED", "Local file no longer exists")
                    return

                vid = Video(
                    video_id=f"local_{file_path.stem[:32]}",
                    title=file_path.stem,
                    url=str(file_path),
                )
                res = process_video_agentic(self.settings, vid)
                self.queue.update_status(task.task_id, "COMPLETED")
                self.logger.info(f"Completed local media task: {task.task_id}")

            elif task.source_type == "youtube":
                videos = get_videos_from_url(task.target, self.settings.ytdlp_binary)
                if not videos:
                    self.queue.update_status(task.task_id, "FAILED", "No videos found at URL")
                    return

                from src.history import completed_ids, append_completed
                completed = completed_ids(self.settings.completed_file)
                for index, video in enumerate(videos, start=1):
                    if video.video_id in completed:
                        self.logger.info(f"Skipping already-completed YouTube video: {video.video_id}")
                        continue
                    self.logger.info(f"Processing YouTube video {index}/{len(videos)}: {video.title}")
                    res = process_video_agentic(self.settings, video)
                    if res.status == "completed":
                        append_completed(self.settings.completed_file, video.video_id)

                self.queue.update_status(task.task_id, "COMPLETED")
                self.logger.info(f"Completed YouTube task: {task.task_id}")

        except Exception as exc:
            self.logger.error(f"Task {task.task_id} failed: {exc}", exc_info=True)
            self.queue.update_status(task.task_id, "FAILED", str(exc))
