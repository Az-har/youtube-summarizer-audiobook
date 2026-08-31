from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.models import Settings, Video


class BasePodcastPublisher(ABC):
    """Abstract base class for podcast publishers."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def publish_episode(
        self,
        video: Video,
        audio_path: Path,
        summary_text: str,
        thumbnail_path: Path | None = None,
        duration_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Publishes the generated audiobook to the podcast platform."""
        raise NotImplementedError
