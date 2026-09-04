from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.models import (
    AudiobookResult,
    EditorialResult,
    IngestionResult,
    Settings,
    TranscriptionResult,
    Video,
)


@dataclass
class AgentContext:
    """
    Execution context holding configurations, target video, workspace directory,
    and strongly-typed stage DTOs.
    """
    settings: Settings
    video: Video
    working_dir: Path
    state: dict[str, Any] = field(default_factory=dict)
    ingestion_result: IngestionResult | None = None
    transcription_result: TranscriptionResult | None = None
    editorial_result: EditorialResult | None = None
    audiobook_result: AudiobookResult | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("Agent"))


class BaseAgent(ABC):
    """Abstract base class for specialized local AI agents in the pipeline."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def run(self, context: AgentContext) -> Any:
        """Execute the agent's specialized task."""
        raise NotImplementedError

    def log(self, context: AgentContext, message: str) -> None:
        context.logger.info(f"[{self.name}] {message}")
        print(f"  [{self.name}] {message}", flush=True)
