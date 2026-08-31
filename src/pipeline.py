from __future__ import annotations

import json
from pathlib import Path

from .downloader import download_audio, get_videos_from_url
from .history import append_completed, completed_ids
from .models import ProcessResult, Settings, Video
from .processing import ProcessingError, prepare_narration, synthesize, transcribe


def process_video(settings: Settings, video: Video) -> ProcessResult:
    from src.agents import process_video_agentic
    return process_video_agentic(settings, video)


def process_url(settings: Settings, url: str, dry_run: bool = False) -> list[ProcessResult]:
    print(f"Fetching video list from: {url} ...")
    videos = get_videos_from_url(url, settings.ytdlp_binary)
    if not videos:
        print("No videos found at the given URL.")
        return []

    completed = completed_ids(settings.completed_file)
    print(f"Found {len(videos)} video(s). {len(completed)} previously completed.")

    results: list[ProcessResult] = []
    for index, video in enumerate(videos, start=1):
        print(f"\n--- [{index}/{len(videos)}] {video.title} ({video.video_id}) ---")
        if video.video_id in completed:
            print(f"Skipping already-completed video: {video.video_id}")
            results.append(ProcessResult(video=video, status="skipped", message="Already completed"))
            continue

        if dry_run:
            print(f"[DRY-RUN] Selected video for processing (Duration: {video.duration_seconds}s, Mode: {video.mode})")
            results.append(ProcessResult(video=video, status="dry_run", message="Selected in dry-run"))
            continue

        try:
            result = process_video(settings, video)
            append_completed(settings.completed_file, video.video_id)
            print(f"Successfully processed {video.video_id}! Summary saved to {result.summary_path}")
        except Exception as exc:
            print(f"Failed processing {video.video_id}: {exc}")
            result = ProcessResult(video=video, status="failed", message=str(exc))
        results.append(result)

    return results


def process_local_files(settings: Settings, input_dir: Path, dry_run: bool = False) -> list[ProcessResult]:
    from .downloader import get_local_media_videos
    videos = get_local_media_videos(input_dir)
    if not videos:
        return []

    completed = completed_ids(settings.completed_file)
    print(f"Found {len(videos)} local media file(s) in {input_dir}. {len(completed)} previously completed.")

    results: list[ProcessResult] = []
    for index, video in enumerate(videos, start=1):
        print(f"\n--- [{index}/{len(videos)}] Local Media: {video.title} ({video.video_id}) ---")
        if video.video_id in completed:
            print(f"Skipping already-completed file: {video.video_id}")
            results.append(ProcessResult(video=video, status="skipped", message="Already completed"))
            continue

        if dry_run:
            print(f"[DRY-RUN] Selected local file for processing: {video.title}")
            results.append(ProcessResult(video=video, status="dry_run", message="Selected in dry-run"))
            continue

        try:
            result = process_video(settings, video)
            append_completed(settings.completed_file, video.video_id)
            print(f"Successfully processed {video.video_id}! Summary saved to {result.summary_path}")
        except Exception as exc:
            print(f"Failed processing {video.video_id}: {exc}")
            result = ProcessResult(video=video, status="failed", message=str(exc))
        results.append(result)

    return results


