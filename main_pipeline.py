from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles to prevent charmap encoding errors
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.config import load_playlist_urls, load_settings
from src.pipeline import process_local_files, process_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="YouTube & Local Audio / Video Summarizer & Audiobook Generator"
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="",
        help="YouTube playlist / video URL or local media path to process",
    )
    parser.add_argument(
        "--file",
        "-f",
        default="",
        help="Path to a text file containing playlist/video URLs (one per line)",
    )
    parser.add_argument(
        "--input",
        "-i",
        default="",
        help="Path to a local directory containing audio/video files (.mp3, .wav, .m4a, .mp4, etc.)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch video/media list only without downloading audio or transcribing",
    )
    parser.add_argument(
        "--test-tts",
        action="store_true",
        help="Test local TTS voice synthesis with sample text",
    )
    parser.add_argument(
        "--language",
        choices=["Tamil", "English"],
        default="English",
        help="Language for TTS test",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    settings = load_settings(root)

    try:
        if args.test_tts:
            from src.processing import synthesize
            lang = args.language
            sample_text = (
                "இது ஒரு மாதிரி தமிழ் குரல் சோதனை." if lang == "Tamil"
                else "This is a sample factual summary for testing local voice synthesis."
            )
            test_dir = settings.data_dir / "test_tts"
            test_dir.mkdir(parents=True, exist_ok=True)
            text_file = test_dir / f"sample_{lang.lower()}.txt"
            text_file.write_text(sample_text, encoding="utf-8")
            out_file = test_dir / f"sample_{lang.lower()}.mp3"
            rendered = synthesize(settings, text_file, out_file, lang)
            if rendered:
                print(f"TTS synthesis successful for {lang}. Audio: {rendered}")
            else:
                print("TTS_COMMAND_TEMPLATE is empty in .env. No audio was generated.")
            return 0

        all_results = []

        # 1. Check for local media directory if specified or in data/input
        input_dir = Path(args.input) if args.input else (settings.data_dir / "input")
        if input_dir.exists() and any(input_dir.iterdir()):
            local_results = process_local_files(settings, input_dir, dry_run=args.dry_run)
            all_results.extend(local_results)

        # 2. Check for YouTube URLs (from CLI arg, --file, or playlists.txt)
        target_urls: list[str] = []
        if args.url:
            # If the positional arg is a local file or directory
            url_path = Path(args.url)
            if url_path.is_file():
                local_dir = url_path.parent
                local_results = process_local_files(settings, local_dir, dry_run=args.dry_run)
                all_results.extend(local_results)
            elif url_path.is_dir():
                local_results = process_local_files(settings, url_path, dry_run=args.dry_run)
                all_results.extend(local_results)
            else:
                target_urls.append(args.url)
        elif args.file:
            target_urls = load_playlist_urls(Path(args.file))
        else:
            default_file = root / "playlists.txt"
            if default_file.exists():
                target_urls = load_playlist_urls(default_file)

        for url in target_urls:
            results = process_url(settings, url, dry_run=args.dry_run)
            all_results.extend(results)

        if not all_results and not target_urls:
            print("Error: No YouTube URL, playlist file, or local audio files found.\n")
            print("Usage examples:")
            print('  py main_pipeline.py "https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID"')
            print('  py main_pipeline.py --input data/input')
            print('  py main_pipeline.py --file playlists.txt')
            return 1

        completed_count = sum(1 for r in all_results if r.status == "completed")
        failed_count = sum(1 for r in all_results if r.status == "failed")
        print(f"\nDone! Processed: {completed_count} completed, {failed_count} failed.")
        return 0 if failed_count == 0 else 2

    except Exception as exc:
        print(f"\nFatal Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

