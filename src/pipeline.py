from __future__ import annotations

import concurrent.futures
from pathlib import Path

from .downloader import get_videos_from_url
from .history import append_completed, completed_ids
from .models import ProcessResult, Settings, Video


def process_video(settings: Settings, video: Video) -> ProcessResult:
    from src.agents import process_video_agentic
    return process_video_agentic(settings, video)


import logging

logger = logging.getLogger("Pipeline")


def _pre_ingest_video(settings: Settings, video: Video) -> None:
    """Helper for pre-fetching and normalizing next video in the pipeline."""
    try:
        from src.agents import IngestionAgent
        from src.agents.base import AgentContext
        working_dir = settings.data_dir / "videos" / video.video_id
        working_dir.mkdir(parents=True, exist_ok=True)
        context = AgentContext(settings=settings, video=video, working_dir=working_dir)
        agent = IngestionAgent()
        agent.run(context)
    except Exception as exc:
        logger.debug("Pre-ingestion for video %s failed or cancelled: %s", video.video_id, exc)


def _execute_video_batch(
    settings: Settings,
    videos: list[Video],
    source_description: str,
    item_label: str = "video",
    dry_run: bool = False,
    concurrent: bool = False,
) -> list[ProcessResult]:
    if not videos:
        return []

    completed = completed_ids(settings.completed_file)
    print(f"Found {len(videos)} {source_description}. {len(completed)} previously completed.")

    results: list[ProcessResult] = []
    uncompleted = [v for v in videos if v.video_id not in completed]
    prefetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1) if concurrent and not dry_run else None

    try:
        for index, video in enumerate(videos, start=1):
            print(f"\n--- [{index}/{len(videos)}] {item_label.title()}: {video.title} ({video.video_id}) ---")
            if video.video_id in completed:
                print(f"Skipping already-completed {item_label}: {video.video_id}")
                results.append(ProcessResult(video=video, status="skipped", message="Already completed"))
                continue

            if dry_run:
                print(f"[DRY-RUN] Selected {item_label} for processing: {video.title} (Duration: {video.duration_seconds}s, Mode: {video.mode})")
                results.append(ProcessResult(video=video, status="dry_run", message="Selected in dry-run"))
                continue

            # If concurrent mode is enabled, pre-fetch/ingest the next video in background
            if prefetch_executor and video in uncompleted:
                current_idx = uncompleted.index(video)
                if current_idx + 1 < len(uncompleted):
                    next_vid = uncompleted[current_idx + 1]
                    prefetch_executor.submit(_pre_ingest_video, settings, next_vid)

            try:
                result = process_video(settings, video)
                if result.status == "completed":
                    append_completed(settings.completed_file, video.video_id)
                    print(f"Successfully processed {video.video_id}! Summary saved to {result.summary_path}")
                else:
                    print(f"Video {video.video_id} did not pass QA ({result.status}). Not marked as completed.")
            except Exception as exc:
                print(f"Failed processing {video.video_id}: {exc}")
                result = ProcessResult(video=video, status="failed", message=str(exc))
            results.append(result)
    finally:
        if prefetch_executor:
            prefetch_executor.shutdown(wait=False)

    return results


def process_url(settings: Settings, url: str, dry_run: bool = False, concurrent: bool = False) -> list[ProcessResult]:
    print(f"Fetching video list from: {url} ...")
    videos = get_videos_from_url(url, settings.ytdlp_binary)
    if not videos:
        print("No videos found at the given URL.")
        return []

    return _execute_video_batch(
        settings,
        videos,
        source_description="video(s)",
        item_label="video",
        dry_run=dry_run,
        concurrent=concurrent,
    )


def process_local_files(settings: Settings, input_dir: Path, dry_run: bool = False, concurrent: bool = False) -> list[ProcessResult]:
    from .downloader import get_local_media_videos
    videos = get_local_media_videos(input_dir)
    if not videos:
        return []

    return _execute_video_batch(
        settings,
        videos,
        source_description=f"local media file(s) in {input_dir}",
        item_label="local file",
        dry_run=dry_run,
        concurrent=concurrent,
    )
