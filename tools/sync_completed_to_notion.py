"""
Backfill / Synchronizer Tool:
Syncs all extracted and completed video summaries from data/videos/ into the user's
Notion database in full Second Brain format.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from mutagen.easyid3 import EasyID3

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_settings
from src.publishers.notion_publisher import NotionPublisher

# Configure console encoding for Windows emojis
sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SyncNotion")


def extract_video_id(url: str) -> str:
    """Extracts YouTube 11-char video ID from various URL formats."""
    match = re.search(r"(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else ""


def build_second_brain_payload(video_dir: Path, video_id: str, title: str) -> dict[str, Any]:
    """
    Constructs a rich Second Brain payload:
    Uses narration.json if available; otherwise intelligently extracts
    Core Thesis, Mental Models, Actionable Takeaways, and Full Script from summary.txt.
    """
    nar_file = video_dir / "narration.json"
    sum_file = video_dir / "summary.txt"

    nar_data: dict[str, Any] = {}
    if nar_file.exists():
        try:
            nar_data = json.loads(nar_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Error reading narration.json in {video_dir}: {e}")

    summary_text = ""
    if sum_file.exists():
        try:
            summary_text = sum_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Error reading summary.txt in {video_dir}: {e}")

    if not summary_text and nar_data.get("script"):
        summary_text = nar_data["script"].strip()

    # 1. Core Thesis
    core_thesis = nar_data.get("core_thesis")
    if not core_thesis:
        paras = [p.strip() for p in summary_text.split("\n\n") if p.strip()]
        if not paras:
            paras = [summary_text]
        intro_regex = re.compile(r"^(hi\b|hello\b|welcome\b|in this video\b|in this episode\b|today we\b)", re.I)
        cleaned_paras = []
        for p in paras:
            sentences = re.split(r"(?<=[.!?])\s+", p)
            filtered = [s for s in sentences if not intro_regex.search(s.strip())]
            if filtered:
                cleaned_paras.append(" ".join(filtered))
        first_p = cleaned_paras[0] if cleaned_paras else paras[0]
        s_list = re.split(r"(?<=[.!?])\s+", first_p)
        core_thesis = " ".join(s_list[:3]) if len(s_list) >= 3 else first_p
        if len(core_thesis) > 400:
            core_thesis = core_thesis[:397] + "..."
        if len(core_thesis) < 50 and len(cleaned_paras) > 1:
            core_thesis = (core_thesis + " " + cleaned_paras[1])[:400]

    # 2. Mental Models & Concepts
    mental_models = nar_data.get("mental_models", [])
    if not mental_models:
        paras = [p.strip() for p in summary_text.split("\n\n") if p.strip()]
        seen_titles = set()
        concept_pattern = re.compile(
            r"\b([A-Z][a-zA-Z0-9\s\-_]{2,30}?)\s+(?:is|are|means|refers to|helps|can be defined as|represents|works by)\s+([^.!?]+[.!?])",
            re.I,
        )
        for p in paras:
            for match in concept_pattern.finditer(p):
                c_title = match.group(1).strip()
                c_desc = match.group(2).strip()
                if (
                    len(c_title) > 3
                    and not any(w in c_title.lower() for w in ["this", "that", "there", "what", "which", "who", "here", "it"])
                    and c_title.lower() not in seen_titles
                    and len(c_desc) > 15
                ):
                    seen_titles.add(c_title.lower())
                    mental_models.append({"title": c_title.title(), "description": f"{c_title} {c_desc}"[:250]})
                if len(mental_models) >= 5:
                    break
            if len(mental_models) >= 5:
                break

        if len(mental_models) < 3:
            for p in paras[1:]:
                s_first = re.split(r"(?<=[.!?])\s+", p)[0]
                if len(s_first) > 40 and len(s_first) < 250:
                    words = [w for w in s_first.split() if w.isalnum()]
                    c_title = " ".join(words[:3]).title()
                    if c_title.lower() not in seen_titles and len(c_title) > 4:
                        seen_titles.add(c_title.lower())
                        mental_models.append({"title": c_title, "description": s_first})
                if len(mental_models) >= 4:
                    break

    # 3. Actionable Takeaways
    actionable_takeaways = nar_data.get("actionable_takeaways", [])
    if not actionable_takeaways:
        action_keywords = re.compile(
            r"\b(should|must|need to|crucial|essential|important to|always|never|make sure|start by|avoid|rule|key is|recommend|step is)\b",
            re.I,
        )
        all_sentences = re.split(r"(?<=[.!?])\s+", summary_text)
        intro_regex = re.compile(r"^(hi\b|hello\b|welcome\b|in this video\b|in this episode\b)", re.I)
        for s in all_sentences:
            s_clean = s.strip()
            if (
                25 < len(s_clean) < 280
                and action_keywords.search(s_clean)
                and not intro_regex.search(s_clean)
                and s_clean not in actionable_takeaways
            ):
                actionable_takeaways.append(s_clean)
            if len(actionable_takeaways) >= 6:
                break

    # Determine language
    language = nar_data.get("target_language", "English")
    if "tamil" in language.lower():
        language = "Tamil"
    else:
        language = "English"

    return {
        "core_thesis": core_thesis,
        "mental_models": mental_models,
        "actionable_takeaways": actionable_takeaways,
        "script": summary_text,
        "target_language": language,
    }


def sync_all_videos() -> None:
    settings = load_settings(PROJECT_ROOT)
    publisher = NotionPublisher(settings)

    if not publisher.is_configured():
        logger.error("Notion Publisher is not properly configured. Check NOTION_API_KEY and NOTION_DATABASE_ID.")
        return

    logger.info(f"Target Notion Database ID: {settings.notion_database_id}")

    # 1. Retrieve all existing database items to avoid duplicate entries
    existing_items: dict[str, dict[str, Any]] = {}
    client = publisher.client

    try:
        pages: list[dict[str, Any]] = []
        if hasattr(client.databases, "query"):
            pages = client.databases.query(database_id=settings.notion_database_id).get("results", [])
        elif hasattr(client, "data_sources"):
            db_meta = client.databases.retrieve(database_id=settings.notion_database_id)
            ds_list = db_meta.get("data_sources", [])
            if ds_list and isinstance(ds_list, list):
                ds_id = ds_list[0].get("id")
                pages = client.data_sources.query(data_source_id=ds_id).get("results", [])

        for p in pages:
            p_id = p.get("id")
            p_props = p.get("properties", {})
            u = publisher._extract_property_text(p_props.get("URL") or {})
            v_id = extract_video_id(u)
            if v_id:
                existing_items[v_id] = {"page_id": p_id, "url": u, "properties": p_props}
            else:
                # Also check title text for url
                t_text = publisher._extract_property_text(p_props.get("Name") or {})
                v_id = extract_video_id(t_text)
                if v_id:
                    existing_items[v_id] = {"page_id": p_id, "url": t_text, "properties": p_props}

        logger.info(f"Found {len(existing_items)} existing entries in Notion: {list(existing_items.keys())}")
    except Exception as exc:
        logger.warning(f"Could not fetch existing items from Notion: {exc}")

    # 2. Iterate through all directories in data/videos/
    videos_base = settings.data_dir / "videos"
    if not videos_base.exists():
        logger.error(f"Directory {videos_base} does not exist!")
        return

    video_dirs = sorted([d for d in videos_base.iterdir() if d.is_dir()])
    logger.info(f"Discovered {len(video_dirs)} video folders in {videos_base}")

    created_count = 0
    updated_count = 0
    skipped_count = 0

    for idx, d in enumerate(video_dirs, start=1):
        vid = d.name
        video_url = f"https://www.youtube.com/watch?v={vid}"

        # Resolve video title
        title = "Unknown Title"
        mp3_file = d / "narration.mp3"
        audio_path_str = ""
        if mp3_file.exists() and mp3_file.stat().st_size > 0:
            audio_path_str = str(mp3_file.relative_to(PROJECT_ROOT) if mp3_file.is_relative_to(PROJECT_ROOT) else mp3_file)
            try:
                tags = EasyID3(mp3_file)
                tags_title = tags.get("title", [])
                if tags_title:
                    title = tags_title[0]
            except Exception:
                pass

        if vid == "fX8pvKklCvA":
            title = "I Found 16 Open Source Repos Worth Building On"
            video_url = "https://youtu.be/fX8pvKklCvA"

        # Build payload
        payload = build_second_brain_payload(d, vid, title)
        lang = payload.get("target_language", "English")

        print(f"\n[{idx}/{len(video_dirs)}] Processing: {vid} | '{title[:50]}'")

        if vid in existing_items:
            page_id = existing_items[vid]["page_id"]
            logger.info(f"  -> Video {vid} already exists as row {page_id}. Updating details...")

            # Check if page already has blocks
            try:
                existing_blocks = client.blocks.children.list(block_id=page_id).get("results", [])
                if not existing_blocks:
                    logger.info("     Page has 0 blocks. Appending full Second Brain summary...")
                    publisher.write_second_brain_summary(
                        item_id=page_id,
                        summary_payload=payload,
                        video_title=title,
                        audio_path=audio_path_str,
                    )
                else:
                    logger.info(f"     Page already has {len(existing_blocks)} blocks. Preserving content.")
            except Exception as b_exc:
                logger.warning(f"     Could not check blocks: {b_exc}")

            # Update page title, URL, Status, Mode, Language
            try:
                client.pages.update(
                    page_id=page_id,
                    properties={
                        "Name": {"title": [{"text": {"content": title[:100]}}]},
                        "URL": {"url": video_url},
                        "Status": {"status": {"name": "Summarized"}},
                        "Mode": {"select": {"name": "Summarize"}},
                        "Language": {"select": {"name": lang}},
                    },
                )
                updated_count += 1
                logger.info(f"  ✅ Updated page {page_id} successfully.")
            except Exception as u_exc:
                logger.error(f"  ❌ Error updating page {page_id}: {u_exc}")
        else:
            # Create new page in database
            logger.info(f"  -> Creating new Notion Second Brain page for {vid}...")
            new_id = publisher.create_second_brain_page(
                video_url=video_url,
                video_title=title,
                summary_payload=payload,
                language=lang,
                mode="Summarize",
                audio_path=audio_path_str,
            )
            if new_id:
                created_count += 1
                logger.info(f"  ✅ Created Notion page {new_id} for {vid}")
            else:
                skipped_count += 1
                logger.error(f"  ❌ Failed to create Notion page for {vid}")

    print("\n" + "=" * 60)
    print("🎉 NOTION SECOND BRAIN SYNC COMPLETE!")
    print(f"   Total Videos Scanned: {len(video_dirs)}")
    print(f"   Newly Created Pages:  {created_count}")
    print(f"   Updated Rows:         {updated_count}")
    print(f"   Failed / Skipped:     {skipped_count}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    sync_all_videos()
