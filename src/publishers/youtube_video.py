from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from src.models import Settings, Video
from src.processing import _find_ffmpeg
from src.publishers.base import BasePodcastPublisher

logger = logging.getLogger("YouTubeVideoPublisher")


def render_podcast_video(
    audio_path: Path,
    thumbnail_path: Path,
    output_video_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    """
    Renders a 1080p MP4 video combining the still-image cover art and audio track.
    """
    ffmpeg_bin = _find_ffmpeg(ffmpeg_binary)
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_bin, "-y",
        "-loop", "1", "-framerate", "2",
        "-i", str(thumbnail_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_video_path),
    ]

    subprocess.run(cmd, capture_output=True, check=True, timeout=600)
    return output_video_path


class YouTubeVideoPublisher(BasePodcastPublisher):
    """
    Renders a 1080p still-image MP4 video from the audiobook and thumbnail,
    and uploads it to YouTube under a designated Podcast Playlist.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.output_dir = settings.data_dir / "output" / "videos"
        self.output_dir.mkdir(parents=True, exist_ok=True)

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

        # 1. Render Video
        safe_title = "".join(c for c in video.title if c.isalnum() or c in (" ", "_", "-")).rstrip()[:80]
        output_mp4 = self.output_dir / f"{safe_title}.mp4"

        thumb = thumbnail_path if (thumbnail_path and thumbnail_path.exists()) else None
        if not thumb:
            # Fallback: create plain solid color cover if thumbnail is missing
            thumb = self.output_dir / f"{video.video_id}_thumb.jpg"
            if not thumb.exists():
                ffmpeg_bin = _find_ffmpeg(self.settings.ffmpeg_binary)
                subprocess.run([
                    ffmpeg_bin, "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=1",
                    "-frames:v", "1", str(thumb)
                ], capture_output=True)

        if not output_mp4.exists() or output_mp4.stat().st_size == 0:
            print(f"  [YouTubePublisher] Rendering 1080p MP4 podcast video for: {video.title}...", flush=True)
            render_podcast_video(audio_path, thumb, output_mp4, self.settings.ffmpeg_binary)
            print(f"  [YouTubePublisher] Render complete: {output_mp4.name}", flush=True)

        # 2. Upload to YouTube (if client_secret is configured)
        client_secret = self._resolve_client_secret()
        if not client_secret or not client_secret.exists():
            return {
                "status": "RENDERED_LOCAL",
                "mode": "youtube_video",
                "video_path": str(output_mp4),
                "notice": f"MP4 rendered locally. Place 'client_secret.json' in project root to enable auto-upload.",
            }

        try:
            uploaded_id = self._upload_to_youtube(video, output_mp4, summary_text, client_secret)
            return {
                "status": "SUCCESS",
                "mode": "youtube_video",
                "uploaded_video_id": uploaded_id,
                "video_url": f"https://www.youtube.com/watch?v={uploaded_id}",
            }
        except Exception as exc:
            logger.warning(f"YouTube upload failed: {exc}")
            return {
                "status": "RENDERED_LOCAL",
                "mode": "youtube_video",
                "video_path": str(output_mp4),
                "error": str(exc),
            }

    def _resolve_client_secret(self) -> Path | None:
        """Finds client_secret.json or any client_secret_*.json in project root."""
        primary = self.settings.root / self.settings.youtube_client_secret_file
        if primary.exists():
            return primary
        for p in self.settings.root.glob("client_secret*.json"):
            if p.is_file():
                return p
        return None

    def _upload_to_youtube(self, video: Video, video_path: Path, summary_text: str, client_secret: Path) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
        token_path = self.settings.root / "token.json"
        creds = None

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        youtube = build("youtube", "v3", credentials=creds)

        title = f"[Audiobook] {video.title}"[:100]
        description = f"{summary_text}\n\nGenerated by Autonomous AI Audiobook Director.\nOriginal Source: {video.url}"[:5000]

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": ["Audiobook", "Podcast", "Summary", "AI"],
                "categoryId": "27",  # Education
            },
            "status": {
                "privacyStatus": self.settings.youtube_privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()
        uploaded_id = response.get("id", "")

        # 3. Add to Podcast Playlist (find or auto-create if not specified)
        playlist_id = self.settings.youtube_podcast_playlist_id
        if uploaded_id:
            try:
                if not playlist_id:
                    # Auto-find or create "AI Audiobooks & Podcasts" playlist
                    playlist_id = self._get_or_create_podcast_playlist(youtube)
                
                if playlist_id:
                    youtube.playlistItems().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "playlistId": playlist_id,
                                "resourceId": {"kind": "youtube#video", "videoId": uploaded_id},
                            }
                        },
                    ).execute()
                    print(f"  [YouTubePublisher] Added to Podcast Playlist: {playlist_id} (Available in YouTube Music Podcasts!)", flush=True)
            except Exception as exc:
                logger.warning(f"Failed to add to podcast playlist: {exc}")

        return uploaded_id

    def _get_or_create_podcast_playlist(self, youtube: Any) -> str:
        """Finds an existing 'AI Audiobooks' playlist or creates a new one."""
        try:
            # Check user's playlists
            req = youtube.playlists().list(part="snippet", mine=True, maxResults=50)
            res = req.execute()
            target_name = "AI Audiobooks & Podcasts"
            for item in res.get("items", []):
                if item.get("snippet", {}).get("title") == target_name:
                    return item.get("id", "")

            # Create new playlist
            create_req = youtube.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": target_name,
                        "description": "Autonomous AI-generated audiobooks and video digests. Accessible via YouTube Music Podcasts.",
                    },
                    "status": {
                        "privacyStatus": self.settings.youtube_privacy_status,
                    },
                },
            )
            created = create_req.execute()
            new_id = created.get("id", "")
            print(f"  [YouTubePublisher] Auto-created new Podcast Playlist: '{target_name}' ({new_id})", flush=True)
            return new_id
        except Exception as exc:
            logger.warning(f"Could not auto-create podcast playlist: {exc}")
            return ""

