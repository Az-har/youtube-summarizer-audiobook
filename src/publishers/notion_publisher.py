from __future__ import annotations

import logging
import re
from typing import Any

from src.models import Settings

logger = logging.getLogger("NotionPublisher")

URL_REGEX = re.compile(r"https?://(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b[-a-zA-Z0-9()@:%_+.~#?&/=]*")


class NotionPublisher:
    """
    Notion Second Brain Synchronizer:
    1. Ingests video URLs and desired modes from Notion Databases or plain Notion Pages.
    2. Writes back structured Second Brain summaries (Thesis, Mental Models, Actionable Takeaways).
    3. Updates publishing status and links once uploaded to YouTube.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = None
        if settings.notion_api_key:
            try:
                from notion_client import Client
                self.client = Client(auth=settings.notion_api_key)
            except Exception as exc:
                logger.warning(f"Failed to initialize Notion client: {exc}")

    def is_configured(self) -> bool:
        return bool(self.client and (self.settings.notion_database_id or self.settings.notion_page_id))

    def fetch_pending_links(self) -> list[dict[str, Any]]:
        """
        Fetches pending links from either Notion Database or plain Notion Page.
        Returns a list of dicts: [{'url': str, 'mode': str, 'item_id': str, 'title': str}]
        """
        if not self.is_configured():
            return []

        pending_items: list[dict[str, Any]] = []

        # 1. Check Notion Database if database ID is configured
        if self.settings.notion_database_id:
            try:
                db_items = self._fetch_from_database(self.settings.notion_database_id)
                pending_items.extend(db_items)
            except Exception as exc:
                logger.error(f"Error querying Notion database {self.settings.notion_database_id}: {exc}")

        # 2. Check plain Notion Page if page ID is configured
        if self.settings.notion_page_id:
            try:
                page_items = self._fetch_from_page(self.settings.notion_page_id)
                pending_items.extend(page_items)
            except Exception as exc:
                logger.error(f"Error querying Notion page {self.settings.notion_page_id}: {exc}")

        return pending_items

    def _fetch_from_database(self, database_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not self.client:
            return results

        # Query database rows: supports databases.query (notion-client 2.x / mocks) and data_sources.query (notion-client 3.x)
        pages: list[dict[str, Any]] = []
        if hasattr(getattr(self.client, "databases", None), "query"):
            try:
                query_res = self.client.databases.query(database_id=database_id)
                pages = query_res.get("results", [])
            except Exception as e:
                logger.debug(f"databases.query failed: {e}")

        if not pages and hasattr(self.client, "data_sources"):
            try:
                db_meta = self.client.databases.retrieve(database_id=database_id)
                ds_list = db_meta.get("data_sources", []) if isinstance(db_meta, dict) else []
                if ds_list and isinstance(ds_list, list) and isinstance(ds_list[0], dict):
                    ds_id = ds_list[0].get("id")
                    query_res = self.client.data_sources.query(data_source_id=ds_id)
                    pages = query_res.get("results", []) if isinstance(query_res, dict) else []
            except Exception as e:
                logger.error(f"Error querying Notion data source for database {database_id}: {e}")

        for page in pages:
            props = page.get("properties", {})
            page_id = page.get("id", "")

            # Check status (e.g. Status != Completed/Done)
            status_val = self._extract_property_text(props.get("Status") or props.get("status") or {})
            if status_val.lower() in ("completed", "done", "published", "in progress", "processing"):
                continue

            # Extract URL
            url = ""
            for key in ("URL", "url", "Link", "link", "Video", "Video URL"):
                if key in props:
                    val = props[key].get("url") or self._extract_property_text(props[key])
                    if val and ("http://" in val or "https://" in val):
                        url = val
                        break

            # If URL not found in properties, check title or name property
            if not url:
                title_prop = props.get("Name") or props.get("Title") or props.get("title") or {}
                extracted = self._extract_urls_from_text(self._extract_property_text(title_prop))
                if extracted:
                    url = extracted[0]

            if not url:
                continue

            # Extract mode tag (Summarize vs Translate)
            mode_val = self._extract_property_text(props.get("Mode") or props.get("mode") or props.get("Type") or {})
            mode = "auto"
            if "translate" in mode_val.lower():
                mode = "translate"
            elif "summar" in mode_val.lower():
                mode = "absolute_summary"

            title = self._extract_property_text(props.get("Name") or props.get("Title") or props.get("title") or {})

            # Extract language selection (Language / Target Language / Lang)
            lang_val = self._extract_property_text(
                props.get("Language")
                or props.get("language")
                or props.get("Target Language")
                or props.get("Target_Language")
                or props.get("Lang")
                or props.get("lang")
                or {}
            )
            language = "auto"
            if "tamil" in lang_val.lower():
                language = "Tamil"
            elif "english" in lang_val.lower():
                language = "English"
            elif lang_val and lang_val.lower() != "auto":
                language = lang_val.strip()

            # Also inspect title tags e.g. [Tamil], [English], [ta], [en]
            if language == "auto" and title:
                t_lower = title.lower()
                if "[tamil]" in t_lower or "[ta]" in t_lower:
                    language = "Tamil"
                elif "[english]" in t_lower or "[en]" in t_lower:
                    language = "English"

            results.append({
                "url": url,
                "mode": mode,
                "language": language,
                "item_id": page_id,
                "title": title or url,
                "source": "database",
            })

        return results

    def _fetch_from_page(self, page_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not self.client:
            return results

        blocks = self.client.blocks.children.list(block_id=page_id).get("results", [])

        for block in blocks:
            b_type = block.get("type", "")
            block_id = block.get("id", "")
            url = ""
            text = ""

            if b_type == "bookmark":
                url = block.get("bookmark", {}).get("url", "")
            elif b_type == "to_do":
                # Skip already checked items
                if block.get("to_do", {}).get("checked", False):
                    continue
                rich_text = block.get("to_do", {}).get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in rich_text)
            elif b_type in ("paragraph", "bulleted_list_item", "numbered_list_item"):
                rich_text = block.get(b_type, {}).get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in rich_text)

            if not url and text:
                # If text contains marker like [Done] or [Completed], skip
                if any(tag in text.lower() for tag in ("[done]", "[completed]", "[published]")):
                    continue
                extracted = self._extract_urls_from_text(text)
                if extracted:
                    url = extracted[0]

            if not url:
                continue

            # Determine mode from text tag e.g. [Translate] or [Summarize]
            mode = "auto"
            if "[translate]" in text.lower():
                mode = "translate"
            elif "[summarize]" in text.lower() or "[summary]" in text.lower():
                mode = "absolute_summary"

            # Determine language from inline text tag e.g. [Tamil] or [English]
            language = "auto"
            t_lower = text.lower()
            if "[tamil]" in t_lower or "[ta]" in t_lower:
                language = "Tamil"
            elif "[english]" in t_lower or "[en]" in t_lower:
                language = "English"

            results.append({
                "url": url,
                "mode": mode,
                "language": language,
                "item_id": block_id,
                "title": text.strip() or url,
                "source": "page_block",
            })

        return results


    def write_second_brain_summary(
        self,
        item_id: str,
        summary_payload: dict[str, Any],
        video_title: str,
        audio_path: str = "",
    ) -> bool:
        """
        Writes a structured 'Second Brain' formatted summary into the Notion item/page.
        Includes Core Thesis, Mental Models, Actionable Takeaways, and Narration Reference.
        """
        if not self.client or not item_id:
            return False

    @staticmethod
    def _chunk_text(text: str, max_len: int = 1800) -> list[str]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[str] = []
        for p in paragraphs:
            if len(p) <= max_len:
                chunks.append(p)
            else:
                words = p.split()
                current: list[str] = []
                curr_len = 0
                for w in words:
                    if curr_len + len(w) + 1 > max_len:
                        if current:
                            chunks.append(" ".join(current))
                        current = [w]
                        curr_len = len(w)
                    else:
                        current.append(w)
                        curr_len += len(w) + 1
                if current:
                    chunks.append(" ".join(current))
        return chunks

    def _build_second_brain_blocks(
        self,
        summary_payload: dict[str, Any],
        video_title: str,
        audio_path: str = "",
    ) -> list[dict[str, Any]]:
        core_thesis = summary_payload.get("core_thesis") or summary_payload.get("script", "")[:350] + "..."
        mental_models = summary_payload.get("mental_models", [])
        actionable_takeaways = summary_payload.get("actionable_takeaways", [])
        script = summary_payload.get("script", "")

        children_blocks: list[dict[str, Any]] = []

        # 1. Divider & Callout for Core Thesis
        children_blocks.append({"object": "block", "type": "divider", "divider": {}})
        children_blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "🎯"},
                "rich_text": [
                    {"type": "text", "text": {"content": "Core Thesis (Signal):\n", "link": None}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": str(core_thesis)[:1800], "link": None}},
                ],
            },
        })

        # 2. Mental Models & Foundational Concepts
        if mental_models:
            children_blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "🧠 Mental Models & Key Concepts"}}],
                },
            })
            for model in mental_models:
                model_title = model.get("title") or "Key Framework"
                model_desc = model.get("description") or str(model)
                children_blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [
                            {"type": "text", "text": {"content": f"{model_title}: ", "link": None}, "annotations": {"bold": True}},
                            {"type": "text", "text": {"content": str(model_desc)[:1800], "link": None}},
                        ],
                    },
                })

        # 3. Actionable Takeaways
        if actionable_takeaways:
            children_blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "⚡ Actionable Takeaways & Tactical Rules"}}],
                },
            })
            for action in actionable_takeaways:
                children_blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": str(action)[:1800]}}],
                    },
                })

        # 4. Audiobook Audio Reference
        if audio_path:
            children_blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "🎧 Audio Digest Reference"}}],
                },
            })
            children_blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"Mastered Audio File: {audio_path}"}},
                    ],
                },
            })

        # 5. Full Summary Script / Transcript
        if script:
            children_blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "📜 Full Summary Script"}}],
                },
            })
            for chunk in self._chunk_text(script, max_len=1800):
                children_blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": chunk}}],
                    },
                })

        return children_blocks

    def write_second_brain_summary(
        self,
        item_id: str,
        summary_payload: dict[str, Any],
        video_title: str,
        audio_path: str = "",
    ) -> bool:
        """
        Writes a structured 'Second Brain' formatted summary into the Notion item/page.
        Includes Core Thesis, Mental Models, Actionable Takeaways, and Narration Reference.
        """
        if not self.client or not item_id:
            return False

        try:
            children_blocks = self._build_second_brain_blocks(
                summary_payload=summary_payload,
                video_title=video_title,
                audio_path=audio_path,
            )

            # Append blocks in batches of 100
            for i in range(0, len(children_blocks), 100):
                self.client.blocks.children.append(block_id=item_id, children=children_blocks[i:i + 100])

            # Update status property if it's a database page
            try:
                self.client.pages.update(
                    page_id=item_id,
                    properties={
                        "Status": {"status": {"name": "Summarized"}},
                    },
                )
            except Exception:
                try:
                    self.client.pages.update(
                        page_id=item_id,
                        properties={
                            "Status": {"select": {"name": "Summarized"}},
                        },
                    )
                except Exception:
                    pass

            logger.info(f"Successfully wrote Second Brain summary to Notion item {item_id}")
            return True
        except Exception as exc:
            logger.error(f"Failed writing Second Brain summary to Notion: {exc}")
            return False

    def create_second_brain_page(
        self,
        video_url: str,
        video_title: str,
        summary_payload: dict[str, Any],
        language: str = "English",
        mode: str = "Summarize",
        audio_path: str = "",
    ) -> str | None:
        """
        Creates a new page in the configured Notion database with full Second Brain formatting.
        """
        if not self.client or not self.settings.notion_database_id:
            return None

        try:
            blocks = self._build_second_brain_blocks(
                summary_payload=summary_payload,
                video_title=video_title,
                audio_path=audio_path,
            )
            first_batch = blocks[:100]
            new_page = self.client.pages.create(
                parent={"database_id": self.settings.notion_database_id},
                properties={
                    "Name": {"title": [{"text": {"content": video_title[:100]}}]},
                    "URL": {"url": video_url},
                    "Status": {"status": {"name": "Summarized"}},
                    "Mode": {"select": {"name": mode}},
                    "Language": {"select": {"name": language}},
                },
                children=first_batch,
            )
            page_id = new_page.get("id")
            if page_id and len(blocks) > 100:
                for i in range(100, len(blocks), 100):
                    self.client.blocks.children.append(block_id=page_id, children=blocks[i:i + 100])
            logger.info(f"Created new Second Brain Notion page {page_id} for {video_title}")
            return page_id
        except Exception as exc:
            logger.error(f"Failed creating Second Brain page in Notion: {exc}")
            return None

    def update_publishing_status(self, item_id: str, youtube_url: str) -> bool:
        """
        Updates status to 'Published to YouTube' and records YouTube URL once uploaded.
        """
        if not self.client or not item_id:
            return False

        updated = False
        try:
            # 1. Attempt database page property update
            self.client.pages.update(
                page_id=item_id,
                properties={
                    "Status": {"status": {"name": "Published"}},
                },
            )
            updated = True
        except Exception:
            try:
                self.client.pages.update(
                    page_id=item_id,
                    properties={
                        "Status": {"select": {"name": "Published"}},
                    },
                )
                updated = True
            except Exception:
                pass

        # 2. Append callout or bookmark block with YouTube URL
        try:
            self.client.blocks.children.append(
                block_id=item_id,
                children=[
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "icon": {"type": "emoji", "emoji": "📺"},
                            "rich_text": [
                                {"type": "text", "text": {"content": "Published to YouTube: ", "link": None}, "annotations": {"bold": True}},
                                {"type": "text", "text": {"content": youtube_url, "link": {"url": youtube_url}}},
                            ],
                        },
                    }
                ],
            )
            updated = True
        except Exception as exc:
            logger.warning(f"Failed to append YouTube callout block in Notion: {exc}")

        return updated

    @staticmethod
    def _extract_urls_from_text(text: str) -> list[str]:
        return URL_REGEX.findall(text)

    @staticmethod
    def _extract_property_text(prop: dict[str, Any]) -> str:
        p_type = prop.get("type", "")
        if p_type == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
        if p_type == "rich_text":
            return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
        if p_type == "select":
            return (prop.get("select") or {}).get("name", "")
        if p_type == "status":
            return (prop.get("status") or {}).get("name", "")
        if p_type == "url":
            return prop.get("url") or ""
        return ""
