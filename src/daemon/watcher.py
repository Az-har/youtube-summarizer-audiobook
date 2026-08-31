from __future__ import annotations

import logging
from pathlib import Path

from src.daemon.queue import TaskQueue

MEDIA_EXTENSIONS = {".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".webm", ".flac", ".ogg", ".aac"}


class IngestionWatcher:
    """Watches local input directory and playlist text files for new incoming work."""

    def __init__(self, queue: TaskQueue, input_dir: Path, playlist_file: Path) -> None:
        self.queue = queue
        self.input_dir = input_dir
        self.playlist_file = playlist_file
        self.logger = logging.getLogger("Watcher")
        self.input_dir.mkdir(parents=True, exist_ok=True)

    def scan_and_enqueue(self) -> int:
        """Scans sources and enqueues new items. Returns count of newly enqueued tasks."""
        enqueued_count = 0

        # 1. Scan Local Input Directory
        if self.input_dir.exists():
            for file_path in self.input_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in MEDIA_EXTENSIONS:
                    task = self.queue.enqueue(str(file_path.resolve()), source_type="local_file")
                    if task:
                        self.logger.info(f"Detected new local media file: {file_path.name}")
                        print(f"  [Watcher] Enqueued local file: {file_path.name}", flush=True)
                        enqueued_count += 1

        # 2. Scan Playlists File
        if self.playlist_file.exists():
            try:
                lines = self.playlist_file.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    url = line.strip()
                    if url and not url.startswith("#") and ("http://" in url or "https://" in url):
                        task = self.queue.enqueue(url, source_type="youtube")
                        if task:
                            self.logger.info(f"Detected new YouTube link: {url}")
                            print(f"  [Watcher] Enqueued YouTube URL: {url}", flush=True)
                            enqueued_count += 1
            except Exception as exc:
                self.logger.warning(f"Error reading playlist file {self.playlist_file}: {exc}")

        return enqueued_count
