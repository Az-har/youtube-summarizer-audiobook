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


if __name__ == "__main__":
    unittest.main()
