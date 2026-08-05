"""Spotify mode: drive this machine's Spotify Connect device through the Web API.

This is the app's original behaviour, lifted out of main.py unchanged in substance so the two modes
(this and cadence.CadenceController) present the same five methods and main.py can bind hotkeys to
either. It still needs a Spotify developer app — SPOTIFY_CLIENT_ID / SECRET / REDIRECT_URI in a .env
next to the exe — and a reachable Spotify client, which is exactly what the Cadence mode exists to
avoid on a network where Spotify is blocked.

Imports of spotipy happen at construction, not at import time, so a Cadence-only portable build never
pays for (or fails on) a Spotify dependency it won't use.
"""

import logging
import threading
import time

from ratelimit import limits, sleep_and_retry

CALLS_PER_SECOND = 1  # Spotify rate limit guard, unchanged
SCAN_INTERVAL = 300   # seconds between device scans when none is found

SCOPE = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-library-modify "
    "user-library-read"
)


@sleep_and_retry
@limits(calls=CALLS_PER_SECOND, period=1)
def rate_limited(func, *args, **kwargs):
    """Rate-limited wrapper for Spotify API calls."""
    return func(*args, **kwargs)


class SpotifyConfigError(Exception):
    """Missing/instantiable-nothing credentials — the app should say so and stop, not half-run."""


class SpotifyController:
    def __init__(self, env, cache_file):
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        missing = [k for k in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI")
                   if not env.get(k)]
        if missing:
            raise SpotifyConfigError(
                f"Spotify mode needs {', '.join(missing)} in a .env next to the exe. "
                "Switch to Cadence mode in Settings if you don't have a Spotify app."
            )
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=env["SPOTIFY_CLIENT_ID"],
            client_secret=env["SPOTIFY_CLIENT_SECRET"],
            redirect_uri=env["SPOTIFY_REDIRECT_URI"],
            scope=SCOPE,
            cache_path=cache_file,
        ))
        logging.info("Spotify client initialized.")
        self.device_id = self._discover_device()
        if self.device_id:
            self._transfer(self.device_id)
        else:
            threading.Thread(target=self._discover_loop, daemon=True).start()

    # ---------------------------------------------------------------- device plumbing
    def _discover_device(self):
        try:
            devices = rate_limited(self.sp.devices).get("devices", [])
            if devices:
                logging.info(f"Discovered device: {devices[0]['name']!r} (id={devices[0]['id']})")
                return devices[0]["id"]
        except Exception as e:
            logging.error(f"Device discovery failed: {e}", exc_info=True)
        return None

    def _discover_loop(self):
        while self.device_id is None:
            logging.info("No device found. Retrying in 5 minutes...")
            time.sleep(SCAN_INTERVAL)
            self.device_id = self._discover_device()
            if self.device_id:
                self._transfer(self.device_id)

    def _transfer(self, device_id):
        try:
            rate_limited(self.sp.transfer_playback, device_id=device_id, force_play=False)
            logging.info(f"Playback transferred to device {device_id}.")
        except Exception as e:
            logging.error(f"Transfer playback failed: {e}", exc_info=True)

    def _device(self):
        if self.device_id is None:
            self.device_id = self._discover_device()
        if self.device_id is None:
            logging.warning("No Spotify device available. Waiting for discovery...")
        return self.device_id

    # ---------------------------------------------------------------- the five actions
    def play_pause(self):
        try:
            dev = self._device()
            if not dev:
                return "No Spotify device found."
            state = rate_limited(self.sp.current_playback)
            if state and state.get("is_playing"):
                rate_limited(self.sp.pause_playback, device_id=dev)
                logging.info("Paused playback.")
            else:
                rate_limited(self.sp.start_playback, device_id=dev)
                logging.info("Started playback.")
        except Exception as e:
            logging.error(f"Play/pause error: {e}", exc_info=True)
        return None

    def next_track(self):
        try:
            dev = self._device()
            if not dev:
                return "No Spotify device found."
            rate_limited(self.sp.next_track, device_id=dev)
            logging.info("Skipped to next track.")
        except Exception as e:
            logging.error(f"Skip next error: {e}", exc_info=True)
        return None

    def previous_track(self):
        try:
            dev = self._device()
            if not dev:
                return "No Spotify device found."
            rate_limited(self.sp.previous_track, device_id=dev)
            logging.info("Skipped to previous track.")
        except Exception as e:
            logging.error(f"Skip previous error: {e}", exc_info=True)
        return None

    def toggle_like(self):
        try:
            playback = rate_limited(self.sp.current_playback)
            if not playback or not playback.get("item"):
                return None
            track_id = playback["item"]["id"]
            liked = rate_limited(self.sp.current_user_saved_tracks_contains, tracks=[track_id])[0]
            if liked:
                rate_limited(self.sp.current_user_saved_tracks_delete, tracks=[track_id])
                logging.info(f"Unliked track {track_id}.")
                return "Unliked"
            rate_limited(self.sp.current_user_saved_tracks_add, tracks=[track_id])
            logging.info(f"Liked track {track_id}.")
            return "Liked"
        except Exception as e:
            logging.error(f"Toggle like error: {e}", exc_info=True)
        return None

    def show_current(self):
        try:
            playback = rate_limited(self.sp.current_playback)
            if playback and playback.get("item"):
                song = playback["item"]["name"]
                artists = ", ".join(a["name"] for a in playback["item"]["artists"])
                logging.info(f"Displayed current song: {song} by {artists}")
                return f"{song} — {artists}"
            return "Nothing playing right now."
        except Exception as e:
            logging.error(f"Show current song error: {e}", exc_info=True)
        return None
