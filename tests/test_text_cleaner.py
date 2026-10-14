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

    def test_sanitize_spoken_transcript(self):
        from src.text_cleaner import sanitize_spoken_transcript
        raw = "Um, welcome back to my channel! So basically, as you can see on the screen, gradient descent minimizes loss. Look at this slide right here."
        sanitized = sanitize_spoken_transcript(raw)
        self.assertNotIn("welcome back to my channel", sanitized)
        self.assertNotIn("as you can see on the screen", sanitized)
        self.assertNotIn("look at this slide", sanitized)
        self.assertIn("gradient descent minimizes loss", sanitized)

    def test_stitch_narrative_continuity(self):
        from src.text_cleaner import stitch_narrative_continuity
        sec1 = "Machine learning relies on mathematical optimization to minimize loss functions."
        sec2 = "In this section, we examine how gradient descent iteratively updates weights (e.g., with small steps)."
        sec3 = "### Conclusion\n- Moving forward, deep networks build directly on these mathematical foundations."

        stitched = stitch_narrative_continuity([sec1, sec2, sec3])
        # Section 2 robotic seam "In this section, " stripped and capitalized
        self.assertIn("We examine how gradient descent iteratively updates weights", stitched)
        self.assertNotIn("In this section,", stitched)
        # Abbreviation expanded for spoken TTS
        self.assertIn("for example, with small steps", stitched)
        # Markdown header and bullets stripped
        self.assertNotIn("###", stitched)
        self.assertNotIn("- Moving forward,", stitched)
        self.assertIn("Deep networks build directly on these mathematical foundations", stitched)


if __name__ == "__main__":
    unittest.main()

