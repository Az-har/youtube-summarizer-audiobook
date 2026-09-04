from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    from mutagen.id3 import (
        APIC,
        CHAP,
        CTOC,
        ID3,
        TALB,
        TCON,
        TIT2,
        TPE1,
        TYER,
        ID3NoHeaderError,
    )
    from mutagen.mp3 import MP3
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

logger = logging.getLogger("MetadataEmbedder")


def embed_audiobook_metadata(
    audio_file: Path,
    title: str,
    artist: str = "AI Audiobook",
    album: str = "YouTube Video Summaries",
    thumbnail_file: Path | None = None,
    chapters: list[dict[str, Any]] | None = None,
    year: str = "",
) -> bool:
    """
    Embeds rich ID3v2 metadata, YouTube thumbnail cover art, and chapter markers
    into an MP3 audiobook file.
    """
    if not audio_file.exists():
        return False

    if not MUTAGEN_AVAILABLE:
        logger.warning("mutagen is not installed. Skipping ID3 metadata embedding.")
        return False

    try:
        try:
            tags = ID3(str(audio_file))
        except ID3NoHeaderError:
            tags = ID3()

        # 1. Text Tags
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist or "AI Audiobook"))
        tags.add(TALB(encoding=3, text=album or "Audiobook Summary"))
        tags.add(TCON(encoding=3, text="Audiobook / Digest"))
        if year:
            tags.add(TYER(encoding=3, text=str(year)))

        # 2. Cover Art / Thumbnail Embedding
        if thumbnail_file and thumbnail_file.exists() and thumbnail_file.stat().st_size > 0:
            image_data = thumbnail_file.read_bytes()
            mime_type = "image/jpeg" if thumbnail_file.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            tags.add(
                APIC(
                    encoding=3,
                    mime=mime_type,
                    type=3,  # Front cover
                    desc="Cover",
                    data=image_data,
                )
            )

        # 3. Chapter Markers Embedding
        if chapters and isinstance(chapters, list) and len(chapters) > 0:
            child_element_ids = []
            for idx, ch in enumerate(chapters, start=1):
                element_id = f"chp{idx}"
                child_element_ids.append(element_id)
                start_ms = int(ch.get("start_time", ch.get("start", 0)) * 1000)
                end_ms = int(ch.get("end_time", ch.get("end", start_ms + 1000)) * 1000)
                ch_title = ch.get("title", f"Chapter {idx}")

                sub_tags = ID3()
                sub_tags.add(TIT2(encoding=3, text=ch_title))

                tags.add(
                    CHAP(
                        element_id=element_id,
                        start_time=start_ms,
                        end_time=end_ms,
                        start_offset=0xFFFFFFFF,
                        end_offset=0xFFFFFFFF,
                        sub_frames=sub_tags.values(),
                    )
                )

            # Table of Contents
            tags.add(
                CTOC(
                    element_id="toc",
                    flags=3,  # Top-level & ordered
                    child_element_ids=child_element_ids,
                    sub_frames=[TIT2(encoding=3, text="Table of Contents")],
                )
            )

        tags.save(str(audio_file), v2_version=3)
        return True

    except Exception as exc:
        logger.warning(f"Failed to embed metadata in {audio_file.name}: {exc}")
        return False
