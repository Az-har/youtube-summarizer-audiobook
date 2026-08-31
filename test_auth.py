"""
One-Time Google OAuth Authorization Helper for YouTube Music Podcast Uploads.
"""
from pathlib import Path
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
    channel_name = channels[0]["snippet"]["title"] if channels else "Unknown Channel"
    print(f"\nSUCCESS! Authenticated as: '{channel_name}'")
    print("token.json created. YouTube Video & Podcast uploads are now fully automated!")
except Exception as exc:
    print(f"\nAuthorization failed: {exc}")
