"""
One-Time Google OAuth Authorization Helper for YouTube Music Podcast Uploads.
"""
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from src.config import load_settings
from src.publishers.youtube_video import get_authenticated_youtube_service

root = Path(__file__).resolve().parent
settings = load_settings(root)
client_secret = root / "client_secret.json"

if not client_secret.exists():
    print(f"Error: 'client_secret.json' not found in {root}")
    exit(1)

print("Starting Google OAuth Sign-in...")
print("A browser window will open. Select your Google account and click Allow.")

try:
    youtube = get_authenticated_youtube_service(settings, client_secret)
    # Validate connection
    res = youtube.channels().list(part="snippet", mine=True).execute()
    channels = res.get("items", [])
    channel_name = channels[0]["snippet"]["title"] if channels else "Your Channel"
    print("\n" + "=" * 55)
    print(f"🎉 SUCCESS! Connected to YouTube Channel: '{channel_name}'")
    print("token.json is active. YouTube Video & Podcast uploads are ready!")
    print("=" * 55)
except Exception as exc:
    err_str = str(exc)
    print("\n" + "=" * 55)
    if "YouTube Data API v3 has not been used" in err_str or "accessNotConfigured" in err_str:
        print("⚠️ ACTION NEEDED: YouTube Data API v3 is not enabled in your Google Cloud project.")
        print("\n👉 Click this link to Enable it in 1 click:")
        print("   https://console.developers.google.com/apis/api/youtube.googleapis.com/overview?project=615324781277")
        print("\nAfter clicking 'Enable', re-run: py test_auth.py")
    else:
        print(f"Authorization error: {exc}")
    print("=" * 55)
