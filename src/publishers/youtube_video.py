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
    """Renders a 1080p MP4 video combining a still-image cover art and an audio track.
    Optimized with ultrafast preset, stillimage tuning, and auto-padding for 10x-20x speedup.
    """
    ffmpeg_bin = _find_ffmpeg(ffmpeg_binary)
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    scale_filter = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    cmd = [
        ffmpeg_bin, "-y",
        "-loop", "1", "-framerate", "1",
        "-i", str(thumbnail_path),
        "-i", str(audio_path),
        "-vf", scale_filter,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "stillimage",
        "-crf", "26",
        "-r", "1",
        "-g", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_video_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=600)
    return output_video_path


def get_authenticated_youtube_service(settings: Settings, client_secret_path: Path) -> Any:
    """Authenticates Google OAuth and returns the YouTube Data API v3 service."""
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/youtube"]
    token_path = settings.root / "token.json"
    creds = None

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        except Exception as exc:
            logger.warning("Failed to load credentials from %s: %s", token_path, exc)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)


class YouTubeVideoPublisher(BasePodcastPublisher):
    """
    Renders 1080p MP4 podcast videos from audiobooks and uploads them directly
    to YouTube under an auto-managed 'AI Audiobooks & Podcasts' playlist.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.output_dir = settings.data_dir / "output" / "videos"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cached_playlist_id: str = settings.youtube_podcast_playlist_id

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

        safe_title = "".join(c for c in video.title if c.isalnum() or c in (" ", "_", "-")).rstrip()[:80] or video.video_id
        output_mp4 = self.output_dir / f"{safe_title}.mp4"

        # 1. Render Video
        thumb = thumbnail_path if (thumbnail_path and thumbnail_path.exists()) else None
        if not thumb:
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

        # 2. Upload to YouTube
        client_secret = self._resolve_client_secret()
        if not client_secret or not client_secret.exists():
            return {
                "status": "RENDERED_LOCAL",
                "mode": "youtube_video",
                "video_path": str(output_mp4),
                "notice": "MP4 rendered locally. Place 'client_secret.json' in project root to enable auto-upload.",
            }

        try:
            print(f"  [YouTubePublisher] Uploading to YouTube channel...", flush=True)
            uploaded_id = self._upload_to_youtube(video, output_mp4, summary_text, client_secret)
            video_url = f"https://youtu.be/{uploaded_id}"
            print(f"  🎉 [YouTubePublisher] Upload Complete! Watch/Listen: {video_url}", flush=True)
            return {
                "status": "SUCCESS",
                "mode": "youtube_video",
                "uploaded_video_id": uploaded_id,
                "video_url": video_url,
            }
        except Exception as exc:
            err_msg = str(exc)
            if "quotaExceeded" in err_msg or "uploadLimitExceeded" in err_msg:
                print(f"  ⚠️ [YouTubePublisher] Daily YouTube upload quota reached (free limit: ~6 videos/day).", flush=True)
                print(f"     Video is safely saved locally at: {output_mp4.name}", flush=True)
            else:
                print(f"  ⚠️ [YouTubePublisher] Upload failed ({exc}). Video saved locally at: {output_mp4.name}", flush=True)
            return {
                "status": "RENDERED_LOCAL",
                "mode": "youtube_video",
                "video_path": str(output_mp4),
                "error": err_msg,
            }

    def _resolve_client_secret(self) -> Path | None:
        primary = self.settings.root / self.settings.youtube_client_secret_file
        if primary.exists():
            return primary
        for p in self.settings.root.glob("client_secret*.json"):
            if p.is_file():
                return p
        return None

    def _upload_to_youtube(self, video: Video, video_path: Path, summary_text: str, client_secret: Path) -> str:
        from googleapiclient.http import MediaFileUpload

        youtube = get_authenticated_youtube_service(self.settings, client_secret)
        author = self.settings.podcast_author or "Azhar"
        title = f"[Audiobook] {video.title} - {author}"[:100]
        description = f"{summary_text}\n\nAudiobook Produced & Narrated by {author}.\nOriginal Source: {video.url}"[:5000]

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": ["Audiobook", "Podcast", "Summary", author, "AI"],
                "categoryId": "27",
            },
            "status": {
                "privacyStatus": self.settings.youtube_privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        res = youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        uploaded_id = res.get("id", "")

        # Add to Playlist with retry
        if uploaded_id:
            playlist_id = self._get_or_create_playlist(youtube)
            if playlist_id:
                import time
                for attempt in range(2):
                    try:
                        youtube.playlistItems().insert(
                            part="snippet",
                            body={
                                "snippet": {
                                    "playlistId": playlist_id,
                                    "resourceId": {"kind": "youtube#video", "videoId": uploaded_id},
                                }
                            },
                        ).execute()
                        print(f"  [YouTubePublisher] Added to Playlist: '{self.settings.podcast_playlist_name}' ({playlist_id})", flush=True)
                        print(f"  [YouTubePublisher] Accessible in YouTube Music -> Library -> Playlists!", flush=True)
                        break
                    except Exception as exc:
                        if attempt == 0:
                            time.sleep(2)
                        else:
                            logger.warning(f"Failed adding video to playlist: {exc}")

        return uploaded_id

    def _get_or_create_playlist(self, youtube: Any) -> str:
        if self._cached_playlist_id:
            return self._cached_playlist_id
        target = self.settings.podcast_playlist_name or "Azhar's AI Audiobooks"
        try:
            res = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
            for item in res.get("items", []):
                if item.get("snippet", {}).get("title") == target:
                    self._cached_playlist_id = item.get("id", "")
                    return self._cached_playlist_id

            created = youtube.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {"title": target, "description": f"Audiobooks and podcasts produced by {self.settings.podcast_author}."},
                    "status": {"privacyStatus": self.settings.youtube_privacy_status},
                },
            ).execute()
            self._cached_playlist_id = created.get("id", "")
            print(f"  [YouTubePublisher] Auto-created Playlist: '{target}' ({self._cached_playlist_id})", flush=True)
            return self._cached_playlist_id
        except Exception as exc:
            logger.warning(f"Could not resolve playlist: {exc}")
            return ""
