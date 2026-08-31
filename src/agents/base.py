from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.models import EvaluationResult, Settings, Video


@dataclass
class AgentContext:
    settings: Settings
    video: Video
    working_dir: Path
    state: dict[str, Any] = field(default_factory=dict)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("Agent"))


class BaseAgent(ABC):
    """Abstract base class for specialized local AI agents in the pipeline."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def run(self, context: AgentContext) -> dict[str, Any]:
        """Execute the agent's specialized task."""
        raise NotImplementedError

    def log(self, context: AgentContext, message: str) -> None:
        context.logger.info(f"[{self.name}] {message}")
        print(f"  [{self.name}] {message}", flush=True)
