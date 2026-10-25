import json
import logging
import time
from pathlib import Path

from src.daemon.queue import TaskQueue
from src.models import Settings
from src.publishers.notion_publisher import NotionPublisher

MEDIA_EXTENSIONS = {".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".webm", ".flac", ".ogg", ".aac"}


class IngestionWatcher:
    """Watches local input directory, playlist text files, and Notion for new incoming work."""

    def __init__(
        self,
        queue: TaskQueue,
        input_dir: Path,
        playlist_file: Path,
        settings: Settings | None = None,
        notion_poll_interval: float = 30.0,
    ) -> None:
        self.queue = queue
        self.input_dir = input_dir
        self.playlist_file = playlist_file
        self.settings = settings
        self.notion_poll_interval = notion_poll_interval
        self._last_notion_poll_time: float = 0.0
        self.logger = logging.getLogger("Watcher")
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self._last_playlist_mtime: float = 0.0
        self._cached_playlist_urls: list[str] = []
        self.notion_publisher: NotionPublisher | None = (
            NotionPublisher(settings) if settings and settings.notion_api_key else None
        )


    def scan_and_enqueue(self, force_notion: bool = False) -> int:
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

        # 2. Scan Playlists File (re-reads only if mtime has changed)
        if self.playlist_file.exists():
            try:
                mtime = self.playlist_file.stat().st_mtime
                if mtime != self._last_playlist_mtime:
                    self._last_playlist_mtime = mtime
                    lines = self.playlist_file.read_text(encoding="utf-8").splitlines()
                    self._cached_playlist_urls = [
                        line.strip()
                        for line in lines
                        if line.strip() and not line.strip().startswith("#") and ("http://" in line or "https://" in line)
                    ]
                for url in self._cached_playlist_urls:
                    task = self.queue.enqueue(url, source_type="youtube")
                    if task:
                        self.logger.info(f"Detected new YouTube link: {url}")
                        print(f"  [Watcher] Enqueued YouTube URL: {url}", flush=True)
                        enqueued_count += 1
            except Exception as exc:
                self.logger.warning(f"Error reading playlist file {self.playlist_file}: {exc}")

        # 3. Scan Notion Database or Page for New Links (throttled to avoid Notion API 429 rate limits)
        now = time.time()
        should_poll_notion = (
            self.notion_publisher
            and self.notion_publisher.is_configured()
            and (force_notion or (now - self._last_notion_poll_time >= self.notion_poll_interval))
        )
        if should_poll_notion:
            self._last_notion_poll_time = now
            try:
                notion_items = self.notion_publisher.fetch_pending_links()
                for item in notion_items:
                    url = item.get("url", "")
                    if not url:
                        continue
                    lang_label = item.get("language", "auto")
                    meta = json.dumps({
                        "notion_item_id": item.get("item_id", ""),
                        "requested_mode": item.get("mode", "auto"),
                        "requested_language": lang_label,
                        "title": item.get("title", ""),
                    })
                    task = self.queue.enqueue(url, source_type="notion", extra_metadata=meta)
                    if task:
                        mode_label = item.get("mode", "auto")
                        self.logger.info(f"Detected new Notion link [{mode_label} / {lang_label}]: {url}")
                        print(f"  [Watcher] Enqueued Notion link [{mode_label} / {lang_label}]: {url}", flush=True)
                        enqueued_count += 1

            except Exception as exc:
                self.logger.warning(f"Error polling Notion for links: {exc}")

        return enqueued_count

