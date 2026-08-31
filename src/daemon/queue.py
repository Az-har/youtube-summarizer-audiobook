from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

TaskStatus = Literal["QUEUED", "PROCESSING", "COMPLETED", "FAILED"]


@dataclass
class Task:
    task_id: str
    source_type: str  # "youtube" or "local_file"
    target: str  # URL or file path
    status: TaskStatus = "QUEUED"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error_message: str = ""
    retries: int = 0


class TaskQueue:
    """Persistent task queue backed by an atomic JSON store."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self._save({})

    def _load(self) -> dict[str, dict]:
        try:
            if self.state_file.exists():
                return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save(self, data: dict[str, dict]) -> None:
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.state_file)

    def enqueue(self, target: str, source_type: str) -> Task | None:
        """Enqueues a target if not already queued or completed."""
        data = self._load()
        # Generate stable ID from target
        task_id = str(abs(hash(target)) % 100000000)
        if task_id in data:
            existing = data[task_id]
            if existing.get("status") in ("QUEUED", "PROCESSING", "COMPLETED"):
                return None  # Already tracked

        task = Task(
            task_id=task_id,
            source_type=source_type,
            target=target,
            status="QUEUED",
        )
        data[task_id] = asdict(task)
        self._save(data)
        return task

    def get_next_queued(self) -> Task | None:
        data = self._load()
        for task_dict in data.values():
            if task_dict.get("status") == "QUEUED":
                return Task(**task_dict)
        return None

    def update_status(self, task_id: str, status: TaskStatus, error_message: str = "") -> None:
        data = self._load()
        if task_id in data:
            data[task_id]["status"] = status
            data[task_id]["updated_at"] = time.time()
            if error_message:
                data[task_id]["error_message"] = error_message
            self._save(data)

    def list_tasks(self) -> list[Task]:
        data = self._load()
        return [Task(**d) for d in data.values()]
