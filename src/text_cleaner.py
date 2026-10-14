import re
from typing import Any, Dict, List

# Pre-compiled module-level regular expressions for high-throughput text cleaning
RE_STUTTER = re.compile(r'\b(\w+)(?:\s+\1\b)+', flags=re.IGNORECASE)
RE_BRACKET_NOISE = re.compile(r'\[(music|applause|laughter|silence|cheering|noise|no audio)\]', flags=re.IGNORECASE)
RE_PAREN_NOISE = re.compile(r'\((music|applause|laughter|silence|cheering|noise)\)', flags=re.IGNORECASE)
RE_MULTI_WHITESPACE = re.compile(r'\s+')
RE_NORMALIZE = re.compile(r'[^\w\s]')

# Spoken disfluency and visual crutch regexes
RE_SPOKEN_FILLER = re.compile(
    r'\b(?:um+|uh+|er+|ah+|you know what I mean|you know|sort of|kind of|basically|like I said)\b',
    flags=re.IGNORECASE,
)
RE_VISUAL_CRUTCHES = re.compile(
    r'\b(?:as (?:you can )?see on (?:the |my )?screen|look at (?:this|the) (?:slide|diagram|chart|screen|figure)|'
    r'on my screen right now|in the diagram below|click (?:here|on this link|the link in the description)|'
    r'as shown on (?:the )?screen)\b',
    flags=re.IGNORECASE,
)
RE_META_CHATTER = re.compile(
    r'\b(?:welcome back to (?:my |the )?channel|in today\'?s video|in this video today|'
    r'leave a comment (?:down )?below|hit (?:the |that )?notification bell|'
    r'don\'t forget to like and subscribe|subscribe to (?:my |the )?channel)\b',
    flags=re.IGNORECASE,
)
RE_ROBOTIC_SEAM_OPENERS = re.compile(
    r'^(?:(?:in this (?:section|part|video|segment|chapter|clip),?|next,?|moving on(?:,| to)?|moving forward(?:,| to)?|'
    r'furthermore,?|continuing from (?:earlier|before),?|the speaker (?:explains|discusses|continues|notes|highlights))\s*)+',
    flags=re.IGNORECASE,
)
RE_MARKDOWN_HEADERS = re.compile(r'^\s*#+.*$', flags=re.MULTILINE)
RE_MARKDOWN_BOLD_ITALIC = re.compile(r'[*_]{1,3}([^*_]+)[*_]{1,3}')
RE_MARKDOWN_CODE = re.compile(r'`+([^`]+)`+')
RE_MARKDOWN_BULLETS = re.compile(r'^\s*[-*•]\s+', flags=re.MULTILINE)

# Abbreviations expanded for neural audio playback
SPOKEN_ABBREVIATIONS = (
    (re.compile(r'\be\.g\.,?\s*', flags=re.IGNORECASE), "for example, "),
    (re.compile(r'\bi\.e\.,?\s*', flags=re.IGNORECASE), "that is, "),
    (re.compile(r'\bvs\.?\s*', flags=re.IGNORECASE), "versus "),
    (re.compile(r'\betc\.?\b', flags=re.IGNORECASE), "and so on"),
    (re.compile(r'\bw/\b', flags=re.IGNORECASE), "with "),
)



def remove_repeated_words(text: str) -> str:
    """
    Removes consecutive repeated words / stutters.
    Example: 'yes yes yes I think' -> 'yes I think'
    """
    return RE_STUTTER.sub(r'\1', text)


def remove_repeated_phrases(text: str, max_phrase_len: int = 32) -> str:
    """
    Removes repeated consecutive phrases that Whisper produces on music/silence/noise loops.
    Handles short stutters up to long looping sentences (up to 32 words) with O(N * L) tuple comparison.
    """
    words = text.split()
    if len(words) < 4:
        return text

    words_lower = tuple(w.lower() for w in words)
    cleaned: list[str] = []
    i = 0
    limit = min(max_phrase_len, len(words) // 2)

    while i < len(words):
        found_repeat = False
        # Check phrase lengths from limit down to 2 words
        for p_len in range(limit, 1, -1):
            if i + 2 * p_len <= len(words):
                phrase1 = words_lower[i : i + p_len]
                phrase2 = words_lower[i + p_len : i + 2 * p_len]
                if phrase1 == phrase2:
                    cleaned.extend(words[i : i + p_len])
                    i += 2 * p_len
                    # Skip any further identical repeats
                    while i + p_len <= len(words) and words_lower[i : i + p_len] == phrase1:
                        i += p_len
                    found_repeat = True
                    break
        if not found_repeat:
            cleaned.append(words[i])
            i += 1

    return " ".join(cleaned)


def clean_transcript_text(text: str) -> str:
    """
    Cleans raw transcript text: removes stutters, repeated phrases, bracket noise, and normalizes spacing.
    """
    if not text:
        return ""
    text = RE_BRACKET_NOISE.sub('', text)
    text = RE_PAREN_NOISE.sub('', text)
    text = remove_repeated_words(text)
    text = remove_repeated_phrases(text, max_phrase_len=32)
    text = RE_MULTI_WHITESPACE.sub(' ', text)
    return text.strip()


def deduplicate_segments(segments: List[Dict[str, Any]], max_consecutive_repeats: int = 2) -> List[Dict[str, Any]]:
    """
    Filters out runaway consecutive repeating segments produced by Whisper hallucinations.
    Avoids re-cleaning already processed text.
    """
    if not segments:
        return []

    deduped: List[Dict[str, Any]] = []
    last_normalized = ""
    repeat_count = 0

    for seg in segments:
        raw_text = seg.get("text", "").strip()
        cleaned = raw_text if seg.get("_cleaned") else clean_transcript_text(raw_text)
        if not cleaned:
            continue

        normalized = RE_NORMALIZE.sub('', cleaned.lower())
        if normalized == last_normalized:
            repeat_count += 1
            if repeat_count > max_consecutive_repeats:
                continue  # Drop hallucinated loop
        else:
            last_normalized = normalized
            repeat_count = 1

        seg_copy = dict(seg)
        seg_copy["text"] = cleaned
        seg_copy["_cleaned"] = True
        deduped.append(seg_copy)

    return deduped


def format_paragraphs(segments: List[Dict[str, Any]], pause_threshold: float = 1.5) -> str:
    """
    Groups segment lines into structured paragraphs based on natural pause duration (gap > pause_threshold).
    Includes timestamps at the start of each paragraph. Avoids redundant re-cleaning.
    """
    if not segments:
        return ""

    paragraphs: list[str] = []
    current_paragraph: list[str] = []
    prev_end = 0.0
    paragraph_start_ts = ""

    for seg in segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        raw_text = seg.get("text", "").strip()
        text = raw_text if seg.get("_cleaned") else clean_transcript_text(raw_text)

        if not text:
            continue

        gap = start - prev_end
        if (gap >= pause_threshold or not current_paragraph) and current_paragraph:
            para_str = f"[{paragraph_start_ts}] " + " ".join(current_paragraph)
            paragraphs.append(para_str)
            current_paragraph = []
            paragraph_start_ts = ""

        if not paragraph_start_ts:
            mins = int(start // 60)
            secs = int(start % 60)
            paragraph_start_ts = f"{mins:02d}:{secs:02d}"

        current_paragraph.append(text)
        prev_end = end

    if current_paragraph:
        para_str = f"[{paragraph_start_ts}] " + " ".join(current_paragraph)
        paragraphs.append(para_str)

    return "\n\n".join(paragraphs)


def sanitize_spoken_transcript(text: str) -> str:
    """
    Sanitizes raw conversational transcript text prior to AI rewriting:
    - Strips bracket/parenthetical noise artifacts
    - Strips speech disfluencies (stutters, repeated phrases, filler words like 'um', 'uh', 'you know')
    - Strips multimodal visual crutches ('as you see on screen', 'look at this slide')
    - Strips YouTube/podcast meta-chatter and subscription calls
    - Normalizes spacing and punctuation
    """
    if not text:
        return ""
    text = clean_transcript_text(text)
    text = RE_VISUAL_CRUTCHES.sub("", text)
    text = RE_META_CHATTER.sub("", text)
    text = RE_SPOKEN_FILLER.sub("", text)
    # Clean dangling punctuation left by removed words
    text = re.sub(r'^[,\s]+', '', text)
    text = re.sub(r'[,\s]+$', '', text)
    text = re.sub(r'\s*,\s*,+', ',', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def stitch_narrative_continuity(sections: List[str]) -> str:
    """
    Stitches multiple consecutive section scripts into a single, cohesive spoken audiobook narrative:
    1. Seam Smoothing: Eliminates repetitive robotic transitional openers from consecutive sections
       (e.g., 'In this section, we examine', 'Next, the speaker discusses', 'Moving forward').
    2. Markdown Stripping: Removes hashes (#), asterisks (*), backticks (`), and bullet lists (-)
       that cause neural TTS (Edge-TTS) to stumble or recite literal punctuation symbols.
    3. Spoken Expansion: Expands written abbreviations (e.g., i.e., vs., etc.) into natural spoken words.
    4. Prosody & Cadence: Normalizes em-dashes and ellipses to generate clean breath pauses in TTS.
    """
    if not sections:
        return ""

    polished_sections: list[str] = []

    for idx, raw_section in enumerate(sections):
        if not raw_section or not raw_section.strip():
            continue

        text = raw_section.strip()

        # 1. Markdown & Structural Formatting Removal (TTS Optimization)
        text = RE_MARKDOWN_HEADERS.sub("", text)
        text = RE_MARKDOWN_BULLETS.sub("", text)
        text = RE_MARKDOWN_CODE.sub(r"\1", text)
        text = RE_MARKDOWN_BOLD_ITALIC.sub(r"\1", text)
        text = text.strip()

        # 2. Seam Smoothing: Strip robotic chunk intro clichés on subsequent sections
        if idx > 0:
            cleaned_opener = RE_ROBOTIC_SEAM_OPENERS.sub("", text).strip()
            # Clean any leading comma or punctuation left behind
            cleaned_opener = re.sub(r'^[,\s;:-]+', '', cleaned_opener).strip()
            if cleaned_opener:
                # Capitalize first character of the seamlessly continued sentence
                text = cleaned_opener[0].upper() + cleaned_opener[1:] if len(cleaned_opener) > 1 else cleaned_opener.upper()

        # 3. Spoken Abbreviation Expansion
        for pattern, replacement in SPOKEN_ABBREVIATIONS:
            text = pattern.sub(replacement, text)

        # 4. Neural TTS Prosody & Breathing Normalization
        text = re.sub(r'\s*(?:---|--|—)\s*', ' — ', text)
        text = re.sub(r'\.{2,}', '...', text)
        lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)

        if text:
            polished_sections.append(text)

    return "\n\n".join(polished_sections)

