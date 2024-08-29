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

# Rate limit constants
CALLS_PER_SECOND = 1  # Define the rate limit: number of calls per second


# Decorator for rate limiting Spotify API calls
@sleep_and_retry
@limits(calls=CALLS_PER_SECOND, period=1)
def rate_limited_spotify_call(func, *args, **kwargs):
    """Rate limited wrapper for Spotify API calls."""
    return func(*args, **kwargs)


# Initialize logging process
def initialize_logging():
    """Initialize logging with specified settings."""
    try:
        # Determine path for application data where logs will be stored
        app_data_path = os.path.join(
            appdirs.user_log_dir("Music Agent ERFFFFF", "MusicAgent"), ""
        )
        os.makedirs(app_data_path, exist_ok=True)  # Ensure the directory exists

        # Configure logging to write to a file within the application data directory
        log_file = os.path.join(app_data_path, "music_agent_control.log")
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s:%(levelname)s:%(message)s",
        )
        logging.info("Logging has been initialized.")
    except Exception as e:
        logging.critical(f"Failed to initialize logging: {e}", exc_info=True)
        sys.exit(1)


initialize_logging()

# Constants for single instance enforcement
MUTEX_NAME = "Global\\MusicAgentMutex"
ERROR_ALREADY_EXISTS = 183


def create_single_instance():
    """Ensure only one instance of the application is running."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = kernel32.CreateMutexW(None, ctypes.wintypes.BOOL(False), MUTEX_NAME)
    last_error = ctypes.get_last_error()

    if last_error == ERROR_ALREADY_EXISTS:
        logging.info("Another instance of the application is already running.")
        sys.exit(0)
    elif last_error != 0:
        logging.error(f"Failed to create mutex: {last_error}")
        sys.exit(1)
    else:
        logging.info("New instance created.")


create_single_instance()


def resource_path(relative_path):
    """Get absolute path to resource, works for development and for PyInstaller."""
    try:
        base_path = sys._MEIPASS  # Try to get the path if bundled by PyInstaller
    except AttributeError:
        base_path = os.path.abspath(
            "."
        )  # Fallback to the current directory if not bundled

    return os.path.join(base_path, relative_path)


# Load configuration from config.json
config_path = resource_path("config.json")
try:
    with open(config_path, "r") as config_file:
        config = json.load(config_file)
        spotify_config = config["spotify"]
    logging.info("Configuration loaded successfully.")
except Exception as e:
    logging.error(
        f"Failed to load configuration from {config_path}: {e}", exc_info=True
    )
    sys.exit(1)

# Set the cache file path for Spotipy
app_data_path = os.path.dirname(config_path)  # Reusing the path setup for logging
cache_file_path = os.path.join(app_data_path, ".cache")

# Initialize Spotify client with OAuth
try:
    scope = "user-read-playback-state user-modify-playback-state user-library-modify user-library-read"
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=spotify_config["client_id"],
            client_secret=spotify_config["client_secret"],
            redirect_uri=spotify_config["redirect_uri"],
            scope=scope,
            cache_path=cache_file_path,
        )
    )
    logging.info("Spotify client initialized successfully.")
except Exception as e:
    logging.error(f"Failed to initialize Spotify client: {e}", exc_info=True)
    sys.exit(1)


# Device control functions
def list_devices():
    """Retrieve and list all devices available under the user's account."""
    try:
        devices = rate_limited_spotify_call(sp.devices)
        print("devices: ", devices)
        if not devices["devices"]:
            print("toto")
            logging.info("No devices found.")
            return []
        logging.info("Available devices:")
        for device in devices["devices"]:
            logging.info(
                f"Name: {device['name']}, Type: {device['type']}, ID: {device['id']}"
            )
        return devices["devices"]
    except Exception as e:
        logging.error(f"Error retrieving devices: {e}", exc_info=True)
        return []


def find_device_by_name(device_name):
    """Find a device by name and return its ID."""
    devices = list_devices()
    for device in devices:
        if device["name"].lower() == device_name.lower():
            logging.info(f"Device found: {device['name']} with ID {device['id']}")
            return device["id"]
    logging.warning(f"No device found with the name {device_name}")
    return None


def transfer_playback_to_device_by_name(device_name):
    """Transfer playback to the device with the specified name."""
    device_id = find_device_by_name(device_name)
    if device_id:
        try:
            rate_limited_spotify_call(
                sp.transfer_playback, device_id=device_id, force_play=False
            )
            logging.info(f"Playback transferred to {device_name} (ID: {device_id})")
        except Exception as e:
            logging.error(
                f"Failed to transfer playback to {device_name}: {e}", exc_info=True
            )
    else:
        logging.error("Failed to find device or transfer playback.")


transfer_playback_to_device_by_name(spotify_config["device_name"])


# Define playback control functions
def play_pause_music():
    """Toggle play/pause based on the current playback state."""
    try:
        playback_state = rate_limited_spotify_call(sp.current_playback)
        if playback_state and playback_state["is_playing"]:
            rate_limited_spotify_call(sp.pause_playback)
            logging.info("Playback paused.")
        else:
            rate_limited_spotify_call(sp.start_playback)
            logging.info("Playback started.")
    except Exception as e:
        logging.error(f"Failed to toggle play/pause: {e}", exc_info=True)


def skip_to_next():
    """Skip to the next track."""
    try:
        rate_limited_spotify_call(sp.next_track)
        logging.info("Skipped to next track.")
    except Exception as e:
        logging.error(f"Failed to skip to next track: {e}", exc_info=True)


def skip_to_previous():
    """Skip to the previous track."""
    try:
        rate_limited_spotify_call(sp.previous_track)
        logging.info("Skipped to previous track.")
    except Exception as e:
        logging.error(f"Failed to skip to previous track: {e}", exc_info=True)


def is_song_liked(track_id):
    """Check if the current song is liked."""
    try:
        results = rate_limited_spotify_call(
            sp.current_user_saved_tracks_contains, tracks=[track_id]
        )
        is_liked = results[0] if results else False
        logging.info(f"Song liked status: {'liked' if is_liked else 'not liked'}.")
        return is_liked
    except spotipy.SpotifyException as e:
        logging.error(f"Error checking if song is liked: {e}", exc_info=True)
        return False


def toggle_like_current_song():
    """Like or unlike the current song based on its liked status."""
    try:
        track_id = sp.current_playback()["item"]["id"]
        if is_song_liked(track_id):
            rate_limited_spotify_call(
                sp.current_user_saved_tracks_delete, tracks=[track_id]
            )
            logging.info(f"Song with ID {track_id} unliked.")
        else:
            rate_limited_spotify_call(
                sp.current_user_saved_tracks_add, tracks=[track_id]
            )
            logging.info(f"Song with ID {track_id} liked.")
    except spotipy.SpotifyException as e:
        logging.error(
            f"Failed to toggle like on song: {e.http_status}, {e.code} - {e.msg}",
            exc_info=True,
        )


# Register keybindings
keyboard.add_hotkey("ctrl+alt+up", play_pause_music)
keyboard.add_hotkey("ctrl+alt+right", skip_to_next)
keyboard.add_hotkey("ctrl+alt+left", skip_to_previous)
keyboard.add_hotkey("ctrl+alt+l", toggle_like_current_song)

logging.info("Spotify control agent started successfully. Waiting for keypress events.")
keyboard.wait()  # Wait indefinitely for keypress events
