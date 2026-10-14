import tempfile
import unittest
from pathlib import Path

from src.models import (
    AudiobookResult,
    EditorialResult,
    EvaluationResult,
    IngestionResult,
    QualityScorecard,
    Settings,
    TranscriptionResult,
)
from src.processing import OllamaClient, semantic_transcript_chunks
from src.storage import StorageManager


class ArchitecturalRefinementsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            root=self.root,
            data_dir=self.root,
            completed_file=self.root / "completed.txt",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_typed_agent_dtos_dict_access_compatibility(self):
        ingest = IngestionResult(
            audio_path=self.root / "audio.m4a",
            duration_seconds=120.5,
            thumbnail_path=self.root / "thumb.jpg",
            chapters=[{"title": "Intro"}],
            wav_16k_path=self.root / "16k.wav",
        )
        self.assertEqual(ingest.duration_seconds, 120.5)
        # Backward-compatible dict access
        self.assertEqual(ingest["duration_seconds"], 120.5)
        self.assertEqual(ingest.get("duration_seconds"), 120.5)
        self.assertIn("wav_16k_path", ingest)
        self.assertIsInstance(ingest.to_dict(), dict)

        trans = TranscriptionResult(
            transcript_path=self.root / "t.json",
            txt_path=self.root / "t.txt",
            transcript_data={"segments": []},
            audit_result=EvaluationResult("transcription", "PASS", 9.5),
        )
        self.assertEqual(trans["audit_result"].score, 9.5)

        edit = EditorialResult(
            narration_path=self.root / "n.json",
            summary_path=self.root / "s.txt",
            script="Summary text",
            critic_score=9.0,
        )
        self.assertEqual(edit["script"], "Summary text")

        audio = AudiobookResult(
            audio_path=self.root / "a.mp3",
            audio_guard_score=9.8,
        )
        self.assertEqual(audio["audio_guard_score"], 9.8)

    def test_semantic_transcript_chunking_with_speech_pauses(self):
        # Segments with a natural silence gap at index 2
        segments = [
            {"start": 0.0, "end": 2.0, "text": "First sentence here."},
            {"start": 2.2, "end": 4.0, "text": "Second continuation sentence."},
            {"start": 4.1, "end": 6.0, "text": "Third sentence ends."},
            # 2-second pause here
            {"start": 8.0, "end": 10.0, "text": "Fourth sentence begins a new topic."},
            {"start": 10.2, "end": 12.0, "text": "Fifth sentence conclusion."},
        ]

        chunks = semantic_transcript_chunks(
            segments, target_characters=50, maximum_characters=200, pause_threshold=1.5
        )
        # The pause between 6.0 and 8.0 should trigger a clean chunk boundary
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(chunks[0]), 3)
        self.assertEqual(len(chunks[1]), 2)

    def test_ollama_client_json_extraction(self):
        # 1. Plain clean JSON
        clean = '{"section_title": "Intro", "script": "Hello"}'
        self.assertEqual(OllamaClient.extract_json(clean)["script"], "Hello")

        # 2. Markdown fence wrapped ```json ... ```
        fenced = '```json\n{"section_title": "Fenced", "script": "Inside markdown"}\n```'
        self.assertEqual(OllamaClient.extract_json(fenced)["script"], "Inside markdown")

        # 3. Preamble text before and trailing text after
        dirty = 'Here is the summary output:\n{"section_title": "Dirty", "script": "Parsed successfully"}\nHope this helps!'
        self.assertEqual(OllamaClient.extract_json(dirty)["script"], "Parsed successfully")

    def test_storage_manager_cache_validation_and_export(self):
        t_path = self.root / "test_transcript.json"
        t_path.write_text('{"segments": [{"start": 0, "end": 100, "text": "OK"}]}', encoding="utf-8")
        
        # Audio doesn't exist, duration is 0, so _is_transcript_complete returns True
        res = StorageManager.is_transcription_cached(t_path, self.root / "dummy.m4a", self.settings)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["segments"]), 1)

        # Safe delete
        self.assertTrue(StorageManager.safe_delete(t_path))
        self.assertFalse(t_path.exists())
        # Deleting non-existent file doesn't crash
        self.assertFalse(StorageManager.safe_delete(t_path))

        # Media export
        audio_src = self.root / "narration.mp3"
        audio_src.write_bytes(b"dummy audio" * 200)
        scorecard = QualityScorecard("test_vid", "PASS", 9.5)
        exported = StorageManager.export_finished_media(
            settings=self.settings,
            video_id="test_vid",
            title="Exported Title",
            audio_src=audio_src,
            script="# Markdown Summary",
            transcript_txt_src=None,
            scorecard=scorecard,
            working_dir=self.root,
        )
        self.assertTrue(Path(exported["audio_path"]).exists())
        self.assertTrue(Path(exported["summary_path"]).exists())
        self.assertTrue(Path(exported["report_path"]).exists())

    def test_deterministic_task_id_sha256(self):
        import hashlib
        from src.daemon.queue import TaskQueue
        target = "https://youtube.com/watch?v=determinism_test"
        expected_id = hashlib.sha256(target.encode("utf-8")).hexdigest()[:16]

        queue1 = TaskQueue(self.root / "q1.json")
        task1 = queue1.enqueue(target, "youtube")
        self.assertEqual(task1.task_id, expected_id)

        # A brand new queue instance with new DB must produce the exact same ID
        queue2 = TaskQueue(self.root / "q2.json")
        task2 = queue2.enqueue(target, "youtube")
        self.assertEqual(task2.task_id, expected_id)

    def test_clean_intermediate_artifacts(self):
        w_dir = self.root / "working_media"
        w_dir.mkdir(parents=True, exist_ok=True)
        wav_file = w_dir / "vid1_16k.wav"
        src_file = w_dir / "source_audio.mp4"
        summary_file = w_dir / "summary.txt"

        wav_file.write_bytes(b"wav content" * 100)
        src_file.write_bytes(b"mp4 content" * 100)
        summary_file.write_text("Preserved summary", encoding="utf-8")

        removed = StorageManager.clean_intermediate_artifacts(w_dir)
        self.assertIn("vid1_16k.wav", removed)
        self.assertIn("source_audio.mp4", removed)
        self.assertFalse(wav_file.exists())
        self.assertFalse(src_file.exists())
        self.assertTrue(summary_file.exists())

    def test_ngram_calculation_with_pretokenized_words(self):
        from src.evaluators.transcript_evaluator import _calculate_ngram_repetition_rate
        tokens = ["repeat", "phrase", "here", "now", "repeat", "phrase", "here", "now"]
        rate = _calculate_ngram_repetition_rate(tokens, n=4)
        self.assertGreater(rate, 0.0)

    def test_chapter_aware_transcript_chunks(self):
        from src.processing import chapter_aware_transcript_chunks
        segments = [
            {"start": 0.0, "end": 10.0, "text": "Segment in chapter 1."},
            {"start": 10.0, "end": 20.0, "text": "Another in chapter 1."},
            {"start": 60.0, "end": 70.0, "text": "Segment in chapter 2."},
            {"start": 70.0, "end": 80.0, "text": "Another in chapter 2."},
        ]
        chapters = [
            {"start_time": 0.0, "end_time": 50.0, "title": "Chapter 1: Intro"},
            {"start_time": 50.0, "end_time": 100.0, "title": "Chapter 2: Core Topic"},
        ]
        chunks = chapter_aware_transcript_chunks(segments, chapters=chapters)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0][0]["_chapter_title"], "Chapter 1: Intro")
        self.assertEqual(chunks[1][0]["_chapter_title"], "Chapter 2: Core Topic")

        # Test fallback when chapters are None
        fallback_chunks = chapter_aware_transcript_chunks(segments, chapters=None)
        self.assertGreaterEqual(len(fallback_chunks), 1)

    def test_deterministic_factual_guard(self):
        from src.evaluators.summary_evaluator import audit_factual_numerical_grounding
        source = "Revenue increased by 15% in 2023, totaling 100 million dollars."

        # Faithful draft matching numbers
        faithful_draft = "In 2023, the growth was 15% with 100 million in revenue."
        deduction, issues = audit_factual_numerical_grounding(source, faithful_draft)
        self.assertEqual(deduction, 0.0)
        self.assertEqual(len(issues), 0)

        # Hallucinated draft altering 15% to 50% and adding ungrounded $500
        hallucinated_draft = "Growth skyrocketed to 50% and generated $500."
        deduction, issues = audit_factual_numerical_grounding(source, hallucinated_draft)
        self.assertGreater(deduction, 0.0)
        self.assertTrue(any("50%" in issue or "500" in issue for issue in issues))

    def test_studio_title_card_generation(self):
        from PIL import Image
        from src.cover_generator import generate_studio_title_card
        out_jpg = self.root / "cover_test.jpg"
        generated = generate_studio_title_card(
            title="The Future of Quantum Computing and AI Architecture",
            author="Azhar",
            duration_seconds=3600.0,
            output_path=out_jpg,
        )
        self.assertTrue(generated.exists())
        self.assertGreater(generated.stat().st_size, 5000)
        with Image.open(str(generated)) as img:
            self.assertEqual(img.size, (1920, 1080))

    def test_vram_guard_concurrency(self):
        from src.vram_guard import VRAMGuard
        # Test basic acquisition and release
        with VRAMGuard.guard("Test_Stage") as acquired:
            self.assertTrue(acquired)

    def test_prepare_chunk_stateful_narrative_context(self):
        from unittest.mock import patch
        from src.models import Video
        from src.processing import _prepare_chunk

        vid = Video("v1", "AI Mastery", "https://url", duration_seconds=1200)
        segs = [{"start": 0.0, "end": 5.0, "text": "As you can see on the screen, this model learns."}]
        tail = "This concludes the initial exploration of neural gradients."

        with patch("src.processing._ollama") as mock_ollama:
            mock_ollama.return_value = {"script": "Continued section.", "removed_segments": [], "warnings": []}
            res = _prepare_chunk(
                self.settings,
                vid,
                "en",
                "English",
                segs,
                section_idx=2,
                total_sections=3,
                chapter_title="Gradient Descent",
                previous_section_tail=tail,
            )
            self.assertIsNotNone(res)
            prompt = mock_ollama.call_args[0][1]
            self.assertIn("Current Chapter: Gradient Descent", prompt)
            self.assertIn("Section: 2 of 3", prompt)
            self.assertIn("NARRATIVE CONTINUATION REQUIREMENT:", prompt)
            self.assertIn(tail, prompt)
            self.assertIn("Translate all visual references", prompt)
            # Sanitization should have stripped 'As you can see on the screen'
            self.assertNotIn("As you can see on the screen", prompt)
            self.assertIn("this model learns", prompt)

    def test_editorial_agent_rolling_continuity_flow(self):
        from unittest.mock import patch
        from src.agents.base import AgentContext
        from src.agents.editorial import EditorialAgent
        from src.models import TranscriptionResult, Video

        vid = Video("v_long", "Long Talk", "https://url", duration_seconds=1800)
        working_dir = self.root / "editorial_test"
        working_dir.mkdir(parents=True, exist_ok=True)
        context = AgentContext(settings=self.settings, video=vid, working_dir=working_dir)

        # 2 distinct chunks
        context.transcription_result = TranscriptionResult(
            transcript_path=working_dir / "t.json",
            txt_path=working_dir / "t.txt",
            transcript_data={
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 10.0, "text": "First chunk text about algorithms."},
                    {"start": 10.0, "end": 20.0, "text": "Second chunk text about data structures."},
                ],
            },
        )

        calls = []

        def fake_prepare(settings, video, src_lang, tgt_lang, segs, section_idx=1, total_sections=1, chapter_title="", previous_section_tail=""):
            calls.append({"idx": section_idx, "tail": previous_section_tail})
            if section_idx == 1:
                return {"script": "Algorithms provide rigorous problem-solving methods. They form computational foundations."}
            else:
                return {"script": "In this section, data structures organize information for efficient algorithmic retrieval."}

        with patch("src.agents.editorial.ensure_ollama_running", return_value=True), \
             patch("src.agents.editorial.chapter_aware_transcript_chunks", return_value=[
                 [{"start": 0.0, "end": 10.0, "text": "First"}],
                 [{"start": 10.0, "end": 20.0, "text": "Second"}],
             ]), \
             patch("src.agents.editorial._prepare_chunk", side_effect=fake_prepare):

            agent = EditorialAgent()
            res = agent.run(context)

            self.assertEqual(len(calls), 2)
            # Chunk 1 starts with no tail
            self.assertEqual(calls[0]["tail"], "")
            # Chunk 2 receives the tail of chunk 1
            self.assertIn("They form computational foundations.", calls[1]["tail"])
            # Final script should have had its robotic seam stripped by stitch_narrative_continuity
            self.assertNotIn("In this section,", res.script)
            self.assertIn("Data structures organize information", res.script)

    def test_judge_summary_robotic_framing_detection(self):
        from src.evaluators.summary_evaluator import judge_summary
        source = "Algorithms structure computation."
        robotic_draft = "In this section, the speaker discusses how algorithms structure computation."
        eval_res = judge_summary(self.settings, source, robotic_draft, "English", "detailed_synthesis")
        self.assertTrue(any("Robotic framing" in issue for issue in eval_res.issues))


if __name__ == "__main__":
    unittest.main()

