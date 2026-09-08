from __future__ import annotations

import logging
import os
import sys
import textwrap
from pathlib import Path

logger = logging.getLogger("CoverGenerator")


def _resolve_font(size: int):
    """Discovers a clean sans-serif TrueType font across Windows/Linux, with graceful default fallback."""
    from PIL import ImageFont

    candidate_paths: list[str] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidate_paths.extend([
            os.path.join(windir, "Fonts", "segoeui.ttf"),
            os.path.join(windir, "Fonts", "arial.ttf"),
            os.path.join(windir, "Fonts", "calibri.ttf"),
        ])
    else:
        candidate_paths.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ])

    for p in candidate_paths:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                continue

    return ImageFont.load_default()


def generate_studio_title_card(
    title: str,
    author: str = "Autonomous AI",
    duration_seconds: float = 0.0,
    output_path: Path | None = None,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """
    Generates a studio-grade 1920x1080 cover card for audio-only media (MP3/WAV/Voice notes).
    Renders an elegant dark slate/indigo gradient, wrapped title typography, and branding badges.
    """
    from PIL import Image, ImageDraw

    if output_path is None:
        raise ValueError("output_path must be specified.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Generate Dark Slate / Indigo Vertical Gradient Canvas
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Gradient: Top-left deep slate (15, 23, 42) -> Bottom-right dark indigo (24, 32, 64)
    r1, g1, b1 = 15, 23, 42
    r2, g2, b2 = 30, 41, 75
    for y in range(height):
        ratio = y / float(height)
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # 2. Draw Subtle Geometric Card Border & Accents
    border_margin = 60
    draw.rounded_rectangle(
        [(border_margin, border_margin), (width - border_margin, height - border_margin)],
        radius=24,
        outline=(56, 189, 248, 80),  # Sky blue accent
        width=3,
    )

    # 3. Branding Tag / Category Badge
    badge_font = _resolve_font(28)
    badge_text = "AUTONOMOUS AUDIOBOOK & PODCAST DIGEST"
    draw.text((border_margin + 50, border_margin + 50), badge_text, fill=(56, 189, 248), font=badge_font)

    # 4. Title Typography with Dynamic Font Scaling & Text Wrapping
    clean_title = (title or "Untitled Audio Summary").strip()
    if len(clean_title) > 120:
        clean_title = clean_title[:117] + "..."

    # Scale font size based on title length
    if len(clean_title) <= 40:
        title_font_size = 68
        wrap_width = 30
    elif len(clean_title) <= 80:
        title_font_size = 54
        wrap_width = 38
    else:
        title_font_size = 44
        wrap_width = 46

    title_font = _resolve_font(title_font_size)
    wrapped_lines = textwrap.wrap(clean_title, width=wrap_width)

    # Center vertical positioning
    line_spacing = int(title_font_size * 1.35)
    total_title_h = len(wrapped_lines) * line_spacing
    start_y = max(border_margin + 160, int((height - total_title_h) / 2) - 40)

    for i, line in enumerate(wrapped_lines):
        y_pos = start_y + (i * line_spacing)
        draw.text((border_margin + 50, y_pos), line, fill=(248, 250, 252), font=title_font)

    # 5. Metadata Footer: Author & Duration Badges
    duration_min = int(duration_seconds // 60)
    dur_str = f"{duration_min} min" if duration_min > 0 else (f"{int(duration_seconds)}s" if duration_seconds > 0 else "")
    author_clean = (author or "Azhar").strip()
    footer_text = f"Curated by {author_clean}" + (f"  •  Duration: {dur_str}" if dur_str else "")

    footer_font = _resolve_font(32)
    draw.text(
        (border_margin + 50, height - border_margin - 80),
        footer_text,
        fill=(148, 163, 184),
        font=footer_font,
    )

    img.save(str(output_path), "JPEG", quality=95)
    logger.info("Generated studio title card: %s", output_path)
    return output_path
