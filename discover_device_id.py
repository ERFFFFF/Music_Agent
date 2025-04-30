#!/usr/bin/env python3
import json
import os
import sys
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth


def load_config(path="config.json"):
    """Load Spotify OAuth settings from config.json."""
    if not os.path.isfile(path):
        print(f"Error: config file not found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r") as f:
        cfg = json.load(f).get("spotify", {})
    required = ["client_id", "client_secret", "redirect_uri"]
    missing = [k for k in required if k not in cfg]
    if missing:
        print(f"Error: missing config keys: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return cfg


def main():
    cfg = load_config()
    scope = "user-read-playback-state"
    auth = SpotifyOAuth(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        redirect_uri=cfg["redirect_uri"],
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
