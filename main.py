import os
import sys
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import keyboard
import logging
import appdirs
import ctypes
from ratelimit import limits, sleep_and_retry
from dotenv import dotenv_values
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
    """Initialize logging to both file and console."""
    app_data_path = os.path.join(
        appdirs.user_log_dir("Music Agent ERFFFFF", "MusicAgent")
    )
    os.makedirs(app_data_path, exist_ok=True)
    log_file = os.path.join(app_data_path, "music_agent_control.log")
    log_format = "%(asctime)s:%(levelname)s:%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
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
env_path = resource_path(".env")
cfg = dotenv_values(env_path)
required_keys = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI",
]
missing = [k for k in required_keys if not cfg.get(k)]
if missing:
    logging.error(f"Missing environment variables in .env: {', '.join(missing)}")
    sys.exit(1)
logging.info("Config loaded.")

# Spotify client
cache_data_dir = appdirs.user_data_dir("Music Agent ERFFFFF", "MusicAgent")
os.makedirs(cache_data_dir, exist_ok=True)
cache_file = os.path.join(cache_data_dir, ".cache")
try:
    scope = (
        "user-read-playback-state "
        "user-modify-playback-state "
        "user-library-modify "
        "user-library-read"
    )
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=cfg["SPOTIFY_CLIENT_ID"],
            client_secret=cfg["SPOTIFY_CLIENT_SECRET"],
            redirect_uri=cfg["SPOTIFY_REDIRECT_URI"],
            scope=scope,
            cache_path=cache_file,
        )
    )
    logging.info("Spotify client initialized.")
except SystemExit:
    raise
except Exception as e:
    logging.error(f"Spotify init error: {e}", exc_info=True)
    sys.exit(1)


DEVICE_ID = None
SCAN_INTERVAL = 300  # seconds between device scans when no device is found


def discover_device():
    """Find the first available Spotify Connect device."""
    try:
        devices = rate_limited_spotify_call(sp.devices)
        available = devices.get("devices", [])
        if available:
            dev = available[0]
            logging.info(f"Discovered device: {dev['name']!r} (id={dev['id']})")
            return dev["id"]
    except Exception as e:
        logging.error(f"Device discovery failed: {e}", exc_info=True)
    return None


def discover_device_loop():
    """Background loop that scans for a device until one is found."""
    global DEVICE_ID
    while DEVICE_ID is None:
        logging.info("No device found. Retrying in 5 minutes...")
        time.sleep(SCAN_INTERVAL)
        DEVICE_ID = discover_device()
        if DEVICE_ID:
            logging.info(f"Device found after retry: {DEVICE_ID}")
            transfer_playback(DEVICE_ID)


def transfer_playback(device_id):
    """Transfer playback to the given device ID."""
    try:
        rate_limited_spotify_call(
            sp.transfer_playback, device_id=device_id, force_play=False
        )
        logging.info(f"Playback transferred to device {device_id}.")
    except Exception as e:
        logging.error(f"Transfer playback failed: {e}", exc_info=True)


# Auto-discover device at startup
DEVICE_ID = discover_device()
if DEVICE_ID:
    transfer_playback(DEVICE_ID)
else:
    threading.Thread(target=discover_device_loop, daemon=True).start()


# Playback controls
def _check_device():
    """Re-discover device if current one is gone, return device_id or None."""
    global DEVICE_ID
    if DEVICE_ID is None:
        DEVICE_ID = discover_device()
    if DEVICE_ID is None:
        logging.warning("No Spotify device available. Waiting for discovery...")
        return None
    return DEVICE_ID


def play_pause_music():
    try:
        dev = _check_device()
        if not dev:
            return
        state = rate_limited_spotify_call(sp.current_playback)
        if state and state.get("is_playing"):
            rate_limited_spotify_call(sp.pause_playback, device_id=dev)
            logging.info("Paused playback.")
        else:
            rate_limited_spotify_call(sp.start_playback, device_id=dev)
            logging.info("Started playback.")
    except Exception as e:
        logging.error(f"Play/pause error: {e}", exc_info=True)


def skip_to_next():
    try:
        dev = _check_device()
        if not dev:
            return
        rate_limited_spotify_call(sp.next_track, device_id=dev)
        logging.info("Skipped to next track.")
    except Exception as e:
        logging.error(f"Skip next error: {e}", exc_info=True)


def skip_to_previous():
    try:
        dev = _check_device()
        if not dev:
            return
        rate_limited_spotify_call(sp.previous_track, device_id=dev)
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


def wake_device():
    """Wake an inactive device by forcing playback then pausing.

    Only works if the device appears in the Spotify Connect device list
    (i.e. the Spotify client is running on it). Truly dormant devices
    (app closed / machine asleep) cannot be woken via the Spotify API.
    """
    try:
        dev = _check_device()
        if not dev:
            return
        rate_limited_spotify_call(
            sp.transfer_playback, device_id=dev, force_play=True
        )
        time.sleep(3)
        rate_limited_spotify_call(sp.pause_playback)
        logging.info(f"Woke (then paused) device {dev}.")
    except Exception as e:
        logging.error(f"Failed to wake device: {e}", exc_info=True)


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
    keyboard.add_hotkey("ctrl+alt+w", wake_device)
    logging.info("Hotkeys registered; awaiting events.")


def main():
    setup_hotkeys()
    keyboard.wait()


if __name__ == "__main__":
    main()
