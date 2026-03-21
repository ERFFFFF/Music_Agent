#!/usr/bin/env python3
import sys
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from dotenv import dotenv_values


def load_config(path=".env"):
    """Load Spotify OAuth settings from .env file."""
    cfg = dotenv_values(path)
    required = ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        print(f"Error: missing .env keys: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return cfg


def main():
    cfg = load_config()
    scope = "user-read-playback-state"
    auth = SpotifyOAuth(
        client_id=cfg["SPOTIFY_CLIENT_ID"],
        client_secret=cfg["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=cfg["SPOTIFY_REDIRECT_URI"],
        scope=scope,
    )
    sp = Spotify(auth_manager=auth)

    devices_resp = sp.devices()
    devices = devices_resp.get("devices", [])
    if not devices:
        print("No active Spotify Connect devices found.")
        return

    print("Available Spotify Connect devices:")
    for dev in devices:
        name = dev.get("name", "<unknown>")
        dev_id = dev.get("id", "<no-id>")
        is_active = dev.get("is_active", False)
        print(f"  • {name!r}")
        print(f"      id:       {dev_id}")
        print(f"      is_active:{is_active}")
        print()


if __name__ == "__main__":
    main()
