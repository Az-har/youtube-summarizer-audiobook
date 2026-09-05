import re
from typing import Any, Dict, List

# Pre-compiled module-level regular expressions for high-throughput text cleaning
RE_STUTTER = re.compile(r'\b(\w+)(?:\s+\1\b)+', flags=re.IGNORECASE)
RE_BRACKET_NOISE = re.compile(r'\[(music|applause|laughter|silence|cheering|noise|no audio)\]', flags=re.IGNORECASE)
RE_PAREN_NOISE = re.compile(r'\((music|applause|laughter|silence|cheering|noise)\)', flags=re.IGNORECASE)
RE_MULTI_WHITESPACE = re.compile(r'\s+')
RE_NORMALIZE = re.compile(r'[^\w\s]')


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
