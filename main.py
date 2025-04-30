import os
import sys
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import keyboard
import logging
import appdirs
import ctypes
import ctypes.wintypes
from ratelimit import limits, sleep_and_retry
import threading
import time

# this string is your “AppUserModelID”
MY_APP_ID = "com.erfffff.musicagent"
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(MY_APP_ID)


# Rate limit constants
CALLS_PER_SECOND = 1  # number of Spotify API calls per second


@sleep_and_retry
@limits(calls=CALLS_PER_SECOND, period=1)
def rate_limited_spotify_call(func, *args, **kwargs):
    """Rate-limited wrapper for Spotify API calls."""
    return func(*args, **kwargs)


def initialize_logging():
    """Initialize file-based logging."""
    app_data_path = os.path.join(
        appdirs.user_log_dir("Music Agent ERFFFFF", "MusicAgent")
    )
    os.makedirs(app_data_path, exist_ok=True)
    log_file = os.path.join(app_data_path, "music_agent_control.log")
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s:%(levelname)s:%(message)s",
    )
    logging.info("Logging initialized.")


initialize_logging()

# Single-instance enforcement
MUTEX_NAME = "Global\\MusicAgentMutex"
ERROR_ALREADY_EXISTS = 183


def create_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = ctypes.get_last_error()
    if last_error == ERROR_ALREADY_EXISTS:
        logging.info("Another instance is already running.")
        sys.exit(0)
    elif last_error != 0:
        logging.error(f"Failed to create mutex: {last_error}")
        sys.exit(1)
    logging.info("Instance lock acquired.")


create_single_instance()


def resource_path(relative_path):
    """Get absolute path to resource (for PyInstaller compatibility)."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# Load config
config_path = resource_path("config.json")
try:
    with open(config_path, "r") as f:
        cfg = json.load(f)["spotify"]
    logging.info("Config loaded.")
except Exception as e:
    logging.error(f"Failed to load config: {e}", exc_info=True)
    sys.exit(1)

# Spotify client
cache_file = os.path.join(os.path.dirname(config_path), ".cache")
try:
    scope = (
        "user-read-playback-state "
        "user-modify-playback-state "
        "user-library-modify "
        "user-library-read"
    )
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            redirect_uri=cfg["redirect_uri"],
            scope=scope,
            cache_path=cache_file,
        )
    )
    logging.info("Spotify client initialized.")
except Exception as e:
    logging.error(f"Spotify init error: {e}", exc_info=True)
    sys.exit(1)


def transfer_playback(device_id):
    """Transfer playback to the given device ID."""
    try:
        rate_limited_spotify_call(
            sp.transfer_playback, device_id=device_id, force_play=False
        )
        logging.info(f"Playback transferred to device {device_id}.")
    except Exception as e:
        logging.error(f"Transfer playback failed: {e}", exc_info=True)


transfer_playback(cfg["device_id"])


# Playback controls (no UI)
def play_pause_music():
    try:
        state = rate_limited_spotify_call(sp.current_playback)
        if state and state.get("is_playing"):
            rate_limited_spotify_call(sp.pause_playback)
            logging.info("Paused playback.")
        else:
            rate_limited_spotify_call(sp.start_playback)
            logging.info("Started playback.")
    except Exception as e:
        logging.error(f"Play/pause error: {e}", exc_info=True)


def skip_to_next():
    try:
        rate_limited_spotify_call(sp.next_track)
        logging.info("Skipped to next track.")
    except Exception as e:
        logging.error(f"Skip next error: {e}", exc_info=True)


def skip_to_previous():
    try:
        rate_limited_spotify_call(sp.previous_track)
        logging.info("Skipped to previous track.")
    except Exception as e:
        logging.error(f"Skip previous error: {e}", exc_info=True)


def toggle_like_current_song():
    try:
        playback = rate_limited_spotify_call(sp.current_playback)
        if not playback or not playback.get("item"):
            return
        track_id = playback["item"]["id"]
        liked = rate_limited_spotify_call(
            sp.current_user_saved_tracks_contains, tracks=[track_id]
        )[0]
        if liked:
            rate_limited_spotify_call(
                sp.current_user_saved_tracks_delete, tracks=[track_id]
            )
            logging.info(f"Unliked track {track_id}.")
        else:
            rate_limited_spotify_call(
                sp.current_user_saved_tracks_add, tracks=[track_id]
            )
            logging.info(f"Liked track {track_id}.")
    except Exception as e:
        logging.error(f"Toggle like error: {e}", exc_info=True)


### does not work
def wake_device_only(device_id):
    try:
        # step 1: wake by playing
        rate_limited_spotify_call(
            sp.transfer_playback, device_ids=[device_id], force_play=True
        )
        # step 2: immediately pause
        rate_limited_spotify_call(sp.pause_playback)
        logging.info(f"Woke (but paused) on {device_id}")
    except Exception as e:
        logging.error(f"Failed to wake device {device_id}: {e}", exc_info=True)


# Windows popup for current song
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_INFO = 0x00000010


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


def create_notify_icon(title, message):
    nid = NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
    nid.uFlags = NIF_INFO
    nid.szInfo = message
    nid.szInfoTitle = title
    ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    time.sleep(5)
    ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))


def show_current_song():
    try:
        playback = rate_limited_spotify_call(sp.current_playback)
        if playback and playback.get("item"):
            song = playback["item"]["name"]
            artists = ", ".join(a["name"] for a in playback["item"]["artists"])
            threading.Thread(
                target=create_notify_icon, args=("Now Playing", f"{song} — {artists}")
            ).start()
            logging.info(f"Displayed current song: {song} by {artists}")
    except Exception as e:
        logging.error(f"Show current song error: {e}", exc_info=True)


# Hotkeys
def setup_hotkeys():
    keyboard.add_hotkey("ctrl+alt+up", play_pause_music)
    keyboard.add_hotkey("ctrl+alt+right", skip_to_next)
    keyboard.add_hotkey("ctrl+alt+left", skip_to_previous)
    keyboard.add_hotkey("ctrl+alt+l", toggle_like_current_song)
    keyboard.add_hotkey("ctrl+alt+c", show_current_song)
    keyboard.add_hotkey("ctrl+alt+w", lambda: wake_device_only(cfg["device_id"]))
    logging.info("Hotkeys registered; awaiting events.")


def main():
    setup_hotkeys()
    keyboard.wait()


if __name__ == "__main__":
    main()
