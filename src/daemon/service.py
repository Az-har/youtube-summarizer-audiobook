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
            settings=settings,
        )
        from src.publishers.notion_publisher import NotionPublisher
        self.notion_publisher: NotionPublisher | None = (
            NotionPublisher(settings) if settings and settings.notion_api_key else None
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

                from src.processing import _get_audio_duration
                duration = 0.0
                try:
                    duration = _get_audio_duration(file_path)
                except Exception:
                    duration = 0.0

                from src.models import sanitize_local_video_id

                vid = Video(
                    video_id=sanitize_local_video_id(file_path.stem),
                    title=file_path.stem,
                    url=str(file_path),
                    duration_seconds=duration,
                    raw={"path": str(file_path), "type": "local"},
                )
                res = process_video_agentic(self.settings, vid)
                if res.status == "completed":
                    from src.history import append_completed
                    append_completed(self.settings.completed_file, vid.video_id)
                    self.queue.update_status(task.task_id, "COMPLETED")
                    self.logger.info(f"Completed local media task: {task.task_id}")
                else:
                    msg = f"Local media failed QA quality check ({res.status})"
                    self.queue.update_status(task.task_id, "FAILED", msg)
                    self.logger.warning(f"Task {task.task_id} failed: {msg}")

            elif task.source_type in ("youtube", "notion"):
                videos = get_videos_from_url(task.target, self.settings.ytdlp_binary)
                if not videos:
                    self.queue.update_status(task.task_id, "FAILED", "No videos found at URL")
                    return

                # Parse extra metadata if present
                meta = {}
                if getattr(task, "extra_metadata", ""):
                    try:
                        import json
                        meta = json.loads(task.extra_metadata)
                    except Exception:
                        meta = {}

                notion_item_id = meta.get("notion_item_id", "")
                requested_mode = meta.get("requested_mode", "auto")

                from src.history import completed_ids, append_completed
                completed = completed_ids(self.settings.completed_file)
                all_succeeded = True
                failure_reasons = []

                for index, video in enumerate(videos, start=1):
                    if notion_item_id:
                        video.notion_page_id = notion_item_id
                    if requested_mode and requested_mode != "auto":
                        video.requested_mode = requested_mode

                    if video.video_id in completed:
                        self.logger.info(f"Skipping already-completed YouTube video: {video.video_id}")
                        continue
                    self.logger.info(f"Processing YouTube video {index}/{len(videos)}: {video.title} (Mode: {video.mode})")
                    res = process_video_agentic(self.settings, video)
                    if res.status == "completed":
                        append_completed(self.settings.completed_file, video.video_id)

                        # Two-way sync back to Notion if item originated from Notion
                        if video.notion_page_id and self.notion_publisher:
                            try:
                                import json
                                working_dir = self.settings.data_dir / "videos" / video.video_id
                                narration_json_path = working_dir / "narration.json"
                                summary_payload = {}
                                if narration_json_path.exists():
                                    summary_payload = json.loads(narration_json_path.read_text(encoding="utf-8"))

                                self.notion_publisher.write_second_brain_summary(
                                    item_id=video.notion_page_id,
                                    summary_payload=summary_payload,
                                    video_title=video.title,
                                    audio_path=str(res.audio_path or ""),
                                )
                                self.logger.info(f"Synced Second Brain summary to Notion page {video.notion_page_id}")
                            except Exception as n_err:
                                self.logger.warning(f"Failed syncing to Notion: {n_err}")
                    else:
                        all_succeeded = False
                        failure_reasons.append(f"{video.video_id}: {res.status}")

                if all_succeeded:
                    self.queue.update_status(task.task_id, "COMPLETED")
                    self.logger.info(f"Completed {task.source_type} task: {task.task_id}")
                else:
                    err_summary = f"Partial failure: {', '.join(failure_reasons)}"
                    self.queue.update_status(task.task_id, "FAILED", err_summary)
                    self.logger.warning(f"Task {task.task_id} finished with errors: {err_summary}")


        except Exception as exc:
            self.logger.error(f"Task {task.task_id} failed: {exc}", exc_info=True)
            self.queue.update_status(task.task_id, "FAILED", str(exc))
