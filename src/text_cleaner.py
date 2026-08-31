import re
from typing import List, Dict, Any


def remove_repeated_words(text: str) -> str:
    """
    Removes consecutive repeated words / stutters.
    Example: 'yes yes yes I think' -> 'yes I think'
    """
    pattern = r'\b(\w+)(?:\s+\1\b)+'
    return re.sub(pattern, r'\1', text, flags=re.IGNORECASE)


def remove_repeated_phrases(text: str, max_phrase_len: int = 4) -> str:
    """
    Removes repeated consecutive phrases that Whisper sometimes produces on music/noise.
    """
    words = text.split()
    if len(words) < 4:
        return text

    cleaned: list[str] = []
    i = 0
    while i < len(words):
        found_repeat = False
        # Check phrase lengths from max_phrase_len down to 2 words
        for p_len in range(max_phrase_len, 1, -1):
            if i + 2 * p_len <= len(words):
                phrase1 = [w.lower() for w in words[i : i + p_len]]
                phrase2 = [w.lower() for w in words[i + p_len : i + 2 * p_len]]
                if phrase1 == phrase2:
                    cleaned.extend(words[i : i + p_len])
                    i += 2 * p_len
                    # Skip any further identical repeats
                    while i + p_len <= len(words) and [w.lower() for w in words[i : i + p_len]] == phrase1:
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
    # Strip common Whisper non-speech noise tags
    text = re.sub(r'\[(music|applause|laughter|silence|cheering|noise|no audio)\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\((music|applause|laughter|silence|cheering|noise)\)', '', text, flags=re.IGNORECASE)
    text = remove_repeated_words(text)
    text = remove_repeated_phrases(text, max_phrase_len=8)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def deduplicate_segments(segments: List[Dict[str, Any]], max_consecutive_repeats: int = 2) -> List[Dict[str, Any]]:
    """
    Filters out runaway consecutive repeating segments produced by Whisper hallucinations.
    If the same or near-identical text repeats more than `max_consecutive_repeats` times,
    further duplicates are discarded.
    """
    if not segments:
        return []

    deduped: List[Dict[str, Any]] = []
    last_normalized = ""
    repeat_count = 0

    for seg in segments:
        raw_text = seg.get("text", "").strip()
        cleaned = clean_transcript_text(raw_text)
        if not cleaned:
            continue

        normalized = re.sub(r'[^\w\s]', '', cleaned.lower())
        if normalized == last_normalized:
            repeat_count += 1
            if repeat_count > max_consecutive_repeats:
                continue  # Drop hallucinated loop
        else:
            last_normalized = normalized
            repeat_count = 1

        seg_copy = dict(seg)
        seg_copy["text"] = cleaned
        deduped.append(seg_copy)

    return deduped


def format_paragraphs(segments: List[Dict[str, Any]], pause_threshold: float = 1.5) -> str:
    """
    Groups segment lines into structured paragraphs based on natural pause duration (gap > pause_threshold).
    Includes timestamps at the start of each paragraph.
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
        text = clean_transcript_text(raw_text)

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
