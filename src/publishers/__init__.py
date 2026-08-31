from __future__ import annotations

from src.models import Settings
from src.publishers.base import BasePodcastPublisher
from src.publishers.rss_feed import RSSPodcastPublisher
from src.publishers.youtube_video import YouTubeVideoPublisher, render_podcast_video
from src.publishers.ytmusic_upload import YTMusicLibraryPublisher


def get_podcast_publisher(settings: Settings) -> BasePodcastPublisher | None:
    """Factory to retrieve the configured podcast publisher."""
    mode = (settings.podcast_publisher_mode or "rss").lower().strip()
    if mode in ("none", "disabled", "false", "0"):
        return None
    elif mode in ("youtube_video", "youtube", "video"):
        return YouTubeVideoPublisher(settings)
    elif mode in ("ytmusic_library", "ytmusic", "library"):
        return YTMusicLibraryPublisher(settings)
    else:  # Default to RSS feed generator
        return RSSPodcastPublisher(settings)


__all__ = [
    "BasePodcastPublisher",
    "RSSPodcastPublisher",
    "YouTubeVideoPublisher",
    "YTMusicLibraryPublisher",
    "get_podcast_publisher",
    "render_podcast_video",
]
