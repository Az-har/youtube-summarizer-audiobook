from __future__ import annotations

import email.utils
import json
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from src.models import Settings, Video
from src.publishers.base import BasePodcastPublisher


class RSSPodcastPublisher(BasePodcastPublisher):
    """
    Maintains a compliant Podcast RSS 2.0 XML feed (with iTunes & YouTube Music podcast spec).
    YouTube Studio / YouTube Music natively ingests this feed to create podcast episodes automatically.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.output_dir = settings.data_dir / "output"
        self.rss_file = self.output_dir / "podcast.xml"
        self.episodes_file = self.output_dir / "podcast_episodes.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_episodes(self) -> list[dict[str, Any]]:
        if self.episodes_file.exists():
            try:
                return json.loads(self.episodes_file.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save_episodes(self, episodes: list[dict[str, Any]]) -> None:
        self.episodes_file.write_text(json.dumps(episodes, indent=2, ensure_ascii=False), encoding="utf-8")

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

        episodes = self._load_episodes()
        # Check if already present
        existing = next((e for e in episodes if e.get("video_id") == video.video_id), None)
        file_size = audio_path.stat().st_size
        audio_filename = audio_path.name
        base_url = self.settings.podcast_base_url.rstrip("/")
        enclosure_url = f"{base_url}/audiobooks/{audio_filename}"

        if existing:
            existing.update({
                "title": video.title,
                "summary": summary_text,
                "enclosure_url": enclosure_url,
                "file_size": file_size,
                "duration_seconds": int(duration_seconds),
                "updated_at": time.time(),
            })
        else:
            episodes.insert(0, {
                "video_id": video.video_id,
                "title": video.title,
                "author": video.channel_title or self.settings.podcast_author,
                "summary": summary_text,
                "enclosure_url": enclosure_url,
                "file_size": file_size,
                "duration_seconds": int(duration_seconds),
                "pub_date": email.utils.formatdate(time.time(), usegmt=True),
                "guid": f"yt-audiobook-{video.video_id}",
            })

        self._save_episodes(episodes)
        self._render_rss_feed(episodes)
        print(f"  [PodcastPublisher] Updated YouTube Music Podcast RSS feed: {self.rss_file.name} ({len(episodes)} episodes)", flush=True)

        return {
            "status": "SUCCESS",
            "mode": "rss",
            "rss_feed_path": str(self.rss_file),
            "episode_count": len(episodes),
        }

    def _render_rss_feed(self, episodes: list[dict[str, Any]]) -> None:
        title = escape(self.settings.podcast_title)
        author = escape(self.settings.podcast_author)
        description = escape(self.settings.podcast_description)
        base_url = self.settings.podcast_base_url.rstrip("/")
        channel_image_url = f"{base_url}/cover.jpg"
        last_build_date = email.utils.formatdate(time.time(), usegmt=True)

        items_xml = []
        for ep in episodes:
            ep_title = escape(ep.get("title", "Untitled Episode"))
            ep_author = escape(ep.get("author", author))
            ep_desc = escape(ep.get("summary", ""))
            ep_url = escape(ep.get("enclosure_url", ""))
            ep_size = ep.get("file_size", 0)
            ep_guid = escape(ep.get("guid", ep.get("video_id", "")))
            ep_date = ep.get("pub_date", last_build_date)
            dur_sec = int(ep.get("duration_seconds", 0))
            dur_formatted = f"{dur_sec // 3600:02d}:{(dur_sec % 3600) // 60:02d}:{dur_sec % 60:02d}"

            item_str = f"""    <item>
      <title>{ep_title}</title>
      <itunes:author>{ep_author}</itunes:author>
      <description>{ep_desc}</description>
      <itunes:summary>{ep_desc}</itunes:summary>
      <enclosure url="{ep_url}" length="{ep_size}" type="audio/mpeg"/>
      <guid isPermaLink="false">{ep_guid}</guid>
      <pubDate>{ep_date}</pubDate>
      <itunes:duration>{dur_formatted}</itunes:duration>
      <itunes:explicit>no</itunes:explicit>
      <itunes:episodeType>full</itunes:episodeType>
    </item>"""
            items_xml.append(item_str)

        rss_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" 
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" 
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{title}</title>
    <link>{base_url}</link>
    <language>en-us</language>
    <itunes:author>{author}</itunes:author>
    <description>{description}</description>
    <itunes:summary>{description}</itunes:summary>
    <itunes:explicit>no</itunes:explicit>
    <itunes:category text="Technology"/>
    <itunes:category text="Education"/>
    <itunes:image href="{channel_image_url}"/>
    <lastBuildDate>{last_build_date}</lastBuildDate>
{chr(10).join(items_xml)}
  </channel>
</rss>
"""
        self.rss_file.write_text(rss_content, encoding="utf-8")
