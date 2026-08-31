import tempfile
import unittest
from pathlib import Path

from src.metadata_embedder import embed_audiobook_metadata
from src.vad import _get_silero_vad_model, apply_silero_vad


class PillarModulesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_embed_audiobook_metadata(self):
        # Create a valid dummy MP3 frame so mutagen can parse ID3
        mp3_file = self.root / "test_audio.mp3"
        # Minimum valid MP3 frame
        mp3_file.write_bytes(b"\xFF\xFB\x90\x64\x00\x00\x00\x00" + b"\x00" * 400)

        thumb_file = self.root / "thumb.jpg"
        thumb_file.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + b"\x00" * 100)

        chapters = [
            {"start_time": 0.0, "end_time": 30.0, "title": "Introduction"},
            {"start_time": 30.0, "end_time": 60.0, "title": "Main Content"},
        ]

        ok = embed_audiobook_metadata(
            audio_file=mp3_file,
            title="Great Audiobook",
            artist="Awesome Channel",
            album="Series 1",
            thumbnail_file=thumb_file,
            chapters=chapters,
        )
        self.assertTrue(ok)

    @unittest.mock.patch("urllib.request.urlretrieve")
    def test_silero_vad_model_path_resolution(self, mock_retrieve):
        def fake_retrieve(url, path):
            Path(path).write_bytes(b"dummy onnx")
        mock_retrieve.side_effect = fake_retrieve
        models_dir = self.root / "models"
        res = _get_silero_vad_model(models_dir)
        self.assertTrue(models_dir.exists())
        self.assertIsNotNone(res)


if __name__ == "__main__":
    unittest.main()
