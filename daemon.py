from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows UTF-8 console output setup
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.config import load_settings
from src.daemon import DaemonService, TaskQueue


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous AI Background Daemon for Audio Transcription & Audiobook Synthesis"
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start the autonomous background daemon worker loop",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display queue status, active tasks, and processing metrics",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Poll interval in seconds (default: 5)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    settings = load_settings(root)

    if args.status:
        state_file = settings.data_dir / "state.json"
        queue = TaskQueue(state_file)
        tasks = queue.list_tasks()
        print(f"\n================ QUEUE STATUS ================")
        print(f"Total Tasks: {len(tasks)}")
        queued = [t for t in tasks if t.status == "QUEUED"]
        processing = [t for t in tasks if t.status == "PROCESSING"]
        completed = [t for t in tasks if t.status == "COMPLETED"]
        failed = [t for t in tasks if t.status == "FAILED"]

        print(f"  - ⏳ Queued     : {len(queued)}")
        print(f"  - ⚙️  Processing : {len(processing)}")
        print(f"  - ✅ Completed  : {len(completed)}")
        print(f"  - ❌ Failed     : {len(failed)}")
        print("==============================================\n")

        for t in tasks:
            print(f"[{t.status:<10}] ({t.source_type}) {t.target}")
            if t.error_message:
                print(f"            Error: {t.error_message}")
        return 0

    # Default action or --start
    daemon = DaemonService(settings=settings, poll_interval=args.interval)
    daemon.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
