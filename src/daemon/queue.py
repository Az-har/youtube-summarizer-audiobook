from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Generator, Literal

logger = logging.getLogger("TaskQueue")

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
    """
    ACID-compliant, thread-safe Task Queue backed by SQLite with automatic
    migration from legacy state.json.
    """

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if self.state_file.suffix.lower() == ".json":
            self.db_path = self.state_file.with_suffix(".db")
            self.json_mirror_path: Path | None = self.state_file
        else:
            self.db_path = self.state_file
            self.json_mirror_path = None

        self._init_db()
        self._auto_migrate_legacy_json()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    error_message TEXT DEFAULT '',
                    retries INTEGER DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")

    def _auto_migrate_legacy_json(self) -> None:
        """Migrates legacy JSON task queue into SQLite if DB is empty."""
        if not self.json_mirror_path or not self.json_mirror_path.exists():
            return

        try:
            raw = self.json_mirror_path.read_text(encoding="utf-8").strip()
            if not raw or raw == "{}":
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                return

            with self._get_connection() as conn:
                count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                if count == 0:
                    for task_dict in data.values():
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO tasks 
                            (task_id, source_type, target, status, created_at, updated_at, error_message, retries)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                task_dict.get("task_id", ""),
                                task_dict.get("source_type", ""),
                                task_dict.get("target", ""),
                                task_dict.get("status", "QUEUED"),
                                task_dict.get("created_at", time.time()),
                                task_dict.get("updated_at", time.time()),
                                task_dict.get("error_message", ""),
                                task_dict.get("retries", 0),
                            ),
                        )
                    logger.info(f"Migrated {len(data)} tasks from {self.json_mirror_path} to SQLite.")
        except Exception as exc:
            logger.debug(f"Legacy migration notice: {exc}")

    def _sync_mirror(self) -> None:
        """Keeps JSON mirror up to date for human readability and inspection."""
        if not self.json_mirror_path:
            return
        try:
            tasks = self.list_tasks()
            data = {t.task_id: asdict(t) for t in tasks}
            temp = self.json_mirror_path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.json_mirror_path)
        except Exception as exc:
            logger.debug(f"Mirror sync failed: {exc}")

    def enqueue(self, target: str, source_type: str) -> Task | None:
        """Enqueues a target if not already tracked or completed."""
        task_id = hashlib.sha256(target.encode("utf-8")).hexdigest()[:16]
        now = time.time()

        with self._get_connection() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row:
                if row["status"] in ("QUEUED", "PROCESSING", "COMPLETED"):
                    return None

            conn.execute(
                """
                INSERT OR REPLACE INTO tasks 
                (task_id, source_type, target, status, created_at, updated_at, error_message, retries)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, source_type, target, "QUEUED", now, now, "", 0),
            )

        task = Task(
            task_id=task_id,
            source_type=source_type,
            target=target,
            status="QUEUED",
            created_at=now,
            updated_at=now,
        )
        self._sync_mirror()
        return task

    def get_next_queued(self) -> Task | None:
        """Atomically retrieves the oldest task currently in QUEUED status."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'QUEUED' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row:
                return Task(
                    task_id=row["task_id"],
                    source_type=row["source_type"],
                    target=row["target"],
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    error_message=row["error_message"],
                    retries=row["retries"],
                )
        return None

    def update_status(self, task_id: str, status: TaskStatus, error_message: str = "") -> None:
        """Updates the status and optional error message of a task."""
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = ?, error_message = ?
                WHERE task_id = ?
                """,
                (status, now, error_message, task_id),
            )
        self._sync_mirror()

    def list_tasks(self) -> list[Task]:
        """Returns all tasks ordered by creation time."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()
            return [
                Task(
                    task_id=r["task_id"],
                    source_type=r["source_type"],
                    target=r["target"],
                    status=r["status"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    error_message=r["error_message"],
                    retries=r["retries"],
                )
                for r in rows
            ]
