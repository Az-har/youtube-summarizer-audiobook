from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models import Settings
from src.publishers.notion_publisher import NotionPublisher


class TestNotionPublisher(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            root=Path("."),
            data_dir=Path("./data"),
            completed_file=Path("./data/completed_videos.txt"),
            notion_api_key="secret_test_key",
            notion_database_id="test_db_id",
            notion_page_id="test_page_id",
            notion_sync_enabled=True,
        )

    @patch("notion_client.Client")
    def test_fetch_from_database(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.databases.query.return_value = {
            "results": [
                {
                    "id": "page_123",
                    "properties": {
                        "Name": {"type": "title", "title": [{"plain_text": "Quantum Physics Lecture"}]},
                        "URL": {"type": "url", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                        "Status": {"type": "status", "status": {"name": "To Process"}},
                        "Mode": {"type": "select", "select": {"name": "Summarize"}},
                        "Language": {"type": "select", "select": {"name": "Tamil"}},
                    },
                },
                {
                    "id": "page_456",
                    "properties": {
                        "Name": {"type": "title", "title": [{"plain_text": "French Cinema Review"}]},
                        "URL": {"type": "url", "url": "https://www.youtube.com/watch?v=abc123xyz"},
                        "Status": {"type": "status", "status": {"name": "Done"}},  # Should be skipped!
                        "Mode": {"type": "select", "select": {"name": "Translate"}},
                    },
                },
            ]
        }

        pub = NotionPublisher(self.settings)
        items = pub._fetch_from_database("test_db_id")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(items[0]["mode"], "absolute_summary")
        self.assertEqual(items[0]["language"], "Tamil")
        self.assertEqual(items[0]["item_id"], "page_123")

    @patch("notion_client.Client")
    def test_fetch_from_page(self, mock_client_cls):
        mock_client = MagicMock()

        mock_client_cls.return_value = mock_client
        mock_client.blocks.children.list.return_value = {
            "results": [
                {
                    "id": "block_1",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "[Tamil] [Translate] Check this out: https://youtu.be/video1"}]
                    },
                },
                {
                    "id": "block_2",
                    "type": "to_do",
                    "to_do": {
                        "checked": False,
                        "rich_text": [{"plain_text": "[Summarize] https://youtu.be/video2"}]
                    },
                },
                {
                    "id": "block_3",
                    "type": "to_do",
                    "to_do": {
                        "checked": True,  # Already checked, should skip!
                        "rich_text": [{"plain_text": "https://youtu.be/video3"}]
                    },
                },
            ]
        }

        pub = NotionPublisher(self.settings)
        items = pub._fetch_from_page("test_page_id")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["mode"], "translate")
        self.assertEqual(items[0]["language"], "Tamil")
        self.assertEqual(items[0]["url"], "https://youtu.be/video1")
        self.assertEqual(items[1]["mode"], "absolute_summary")
        self.assertEqual(items[1]["language"], "auto")
        self.assertEqual(items[1]["url"], "https://youtu.be/video2")


    @patch("notion_client.Client")
    def test_write_second_brain_summary(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        pub = NotionPublisher(self.settings)
        summary_payload = {
            "core_thesis": "Focus on high leverage systems over raw motivation.",
            "mental_models": [
                {"title": "Compounding Effort", "description": "Small 1% daily improvements compound exponentially."},
            ],
            "actionable_takeaways": [
                "Audit weekly energy sinks.",
                "Automate repetitive daily tasks.",
            ],
        }

        success = pub.write_second_brain_summary(
            item_id="page_123",
            summary_payload=summary_payload,
            video_title="Systems vs Goals",
            audio_path="/data/audio.mp3",
        )

        self.assertTrue(success)
        mock_client.blocks.children.append.assert_called_once()
        args, kwargs = mock_client.blocks.children.append.call_args
        self.assertEqual(kwargs["block_id"], "page_123")
        children = kwargs["children"]
        # Should contain divider, callout (thesis), heading_2, bullet items, heading_3 (audio)
        self.assertGreaterEqual(len(children), 5)

    @patch("notion_client.Client")
    def test_update_publishing_status(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        pub = NotionPublisher(self.settings)
        success = pub.update_publishing_status("page_123", "https://youtube.com/watch?v=live123")

        self.assertTrue(success)
        mock_client.pages.update.assert_called_once()
        mock_client.blocks.children.append.assert_called_once()

    @patch("notion_client.Client")
    def test_data_source_id_caching(self, mock_client_cls):
        mock_client = MagicMock()
        del mock_client.databases.query  # Force data_sources query path
        mock_client.databases.retrieve.return_value = {
            "data_sources": [{"id": "ds_cached_123"}]
        }
        mock_client.data_sources.query.return_value = {"results": []}
        mock_client_cls.return_value = mock_client

        pub = NotionPublisher(self.settings)
        pub._fetch_from_database("db_test_id")
        pub._fetch_from_database("db_test_id")

        # retrieve should be called only once because ds_id is cached!
        mock_client.databases.retrieve.assert_called_once_with(database_id="db_test_id")
        self.assertEqual(mock_client.data_sources.query.call_count, 2)

    def test_fallback_second_brain_extraction(self):
        sample_script = (
            "Wealth creation is a crucial topic in personal finance. Compounding Interest means earning interest on past interest over long periods.\n\n"
            "An Emergency Fund is savings set aside for three to six months of expenses. You must always maintain an emergency reserve before investing in stocks.\n\n"
            "Asset Allocation represents dividing investments across equity, debt, and gold to minimize volatility."
        )
        thesis, models, takeaways = NotionPublisher.extract_fallback_second_brain(sample_script)
        self.assertTrue(len(thesis) > 10)
        self.assertGreaterEqual(len(models), 1)
        self.assertGreaterEqual(len(takeaways), 1)
        self.assertTrue(any("Emergency Fund" in m["title"] or "Compounding" in m["title"] or "Asset" in m["title"] for m in models))


if __name__ == "__main__":
    unittest.main()
