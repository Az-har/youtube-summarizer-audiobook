import unittest
from src.text_cleaner import (
    remove_repeated_words,
    remove_repeated_phrases,
    clean_transcript_text,
    format_paragraphs,
)


class TextCleanerTests(unittest.TestCase):

    def test_remove_repeated_words(self):
        text = "yes yes yes I think this is very very good"
        cleaned = remove_repeated_words(text)
        self.assertEqual(cleaned, "yes I think this is very good")

    def test_remove_repeated_phrases(self):
        text = "thank you for watching thank you for watching this video"
        cleaned = remove_repeated_phrases(text)
        self.assertEqual(cleaned, "thank you for watching this video")

    def test_clean_transcript_text(self):
        raw = "   so so I said    we should go go now   "
        cleaned = clean_transcript_text(raw)
        self.assertEqual(cleaned, "so I said we should go now")

    def test_format_paragraphs_with_silence_gaps(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello world."},
            {"start": 2.5, "end": 4.0, "text": "This is part of the first thought."},
            {"start": 6.0, "end": 8.0, "text": "After a 2 second pause, new paragraph starts."},
        ]
        result = format_paragraphs(segments, pause_threshold=1.5)
        self.assertIn("[00:00] Hello world. This is part of the first thought.", result)
    def test_deduplicate_segments(self):
        from src.text_cleaner import deduplicate_segments
        looping_segments = [
            {"start": 0.0, "end": 2.0, "text": "Valid sentence here."},
            {"start": 2.0, "end": 4.0, "text": "Thank you for watching."},
            {"start": 4.0, "end": 6.0, "text": "Thank you for watching."},
            {"start": 6.0, "end": 8.0, "text": "Thank you for watching."},
            {"start": 8.0, "end": 10.0, "text": "Thank you for watching."},
            {"start": 10.0, "end": 12.0, "text": "Next topic begins now."},
        ]
        deduped = deduplicate_segments(looping_segments, max_consecutive_repeats=2)
        # Should retain only 2 repeats of "Thank you for watching", dropping 3rd & 4th
        self.assertEqual(len(deduped), 4)
        texts = [s["text"] for s in deduped]
        self.assertEqual(texts, ["Valid sentence here.", "Thank you for watching.", "Thank you for watching.", "Next topic begins now."])


if __name__ == "__main__":
    unittest.main()
