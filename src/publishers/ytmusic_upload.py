from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.models import Settings, Video
from src.publishers.base import BasePodcastPublisher

logger = logging.getLogger("YTMusicLibraryPublisher")


class YTMusicLibraryPublisher(BasePodcastPublisher):
    """
    Directly uploads generated audiobooks into the user's personal YouTube Music Library (Uploads tab).
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def publish_episode(
        self,
        video: Video,
        audio_path: Path,
        summary_text: str,
        thumbnail_path: Path | None = None,
        duration_seconds: float = 0.0,
    ) -> dict[str, Any]:
        if not audio_path.exists():
            return {"status": "FAILED", "error": "Audio file does not exist."}

        auth_file = self.settings.root / self.settings.ytmusic_auth_file
        if not auth_file.exists():
            return {
                "status": "AUTH_REQUIRED",
                "mode": "ytmusic_library",
                "notice": f"YTMusic auth file '{self.settings.ytmusic_auth_file}' not found. Run 'ytmusicapi oauth' to authenticate.",
            }

        try:
            from ytmusicapi import YTMusic

            ytmusic = YTMusic(str(auth_file))
            print(f"  [YTMusicPublisher] Uploading to personal YouTube Music library: {audio_path.name}...", flush=True)
            response = ytmusic.upload_song(str(audio_path))
            print(f"  [YTMusicPublisher] Upload completed! Status: {response}", flush=True)

            return {
                "status": "SUCCESS",
                "mode": "ytmusic_library",
                "response": str(response),
            }
        except Exception as exc:
            logger.warning(f"YTMusic upload failed: {exc}")
            return {
                "status": "FAILED",
                "mode": "ytmusic_library",
                "error": str(exc),
            }
