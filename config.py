import os
import json
import logging
import appdirs


DEFAULT_HOTKEYS = {
    "play_pause": "ctrl+alt+up",
    "next_track": "ctrl+alt+right",
    "previous_track": "ctrl+alt+left",
    "like_unlike": "ctrl+alt+l",
    "show_current": "ctrl+alt+c",
}


def _config_path():
    """Return the full path to config.json in AppData."""
    data_dir = appdirs.user_data_dir("Music Agent ERFFFFF", "MusicAgent")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "config.json")


def load_config():
    """Load config from JSON file. Returns dict with 'hotkeys' key.
    If file is missing or corrupt, returns defaults."""
    path = _config_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            hotkeys = {**DEFAULT_HOTKEYS, **data.get("hotkeys", {})}
            return {"hotkeys": hotkeys}
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Config file corrupt or unreadable, using defaults: {e}")
    return {"hotkeys": dict(DEFAULT_HOTKEYS)}


def save_config(config):
    """Write config dict to JSON file. Uses atomic write via temp file."""
    path = _config_path()
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_path, path)
        logging.info(f"Config saved to {path}")
    except OSError as e:
        logging.error(f"Failed to save config: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
