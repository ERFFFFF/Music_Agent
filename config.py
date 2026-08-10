"""Everything the app remembers, in ONE file: cadence_config.txt, next to the app itself.

Format is JSON (readable, and one parser instead of three) holding the whole configuration:

    {
      "mode": "cadence",
      "cadence_url": "https://cadence.example.com",   # YOUR server — no default is baked in
      "cadence_session": "<signed session cookie — this is a credential>",
      "cf_access_client_id": "", "cf_access_client_secret": "",
      "spotify_client_id": "", "spotify_client_secret": "", "spotify_redirect_uri": "...",
      "hotkeys": {"play_pause": "ctrl+alt+up", ...}
    }

It replaces the old config.json + cadence_session.json + .env trio, which scattered one app's settings
across three files and two directories; those are migrated in on first run and then left alone.

SECURITY: `cadence_session` is a bearer credential (a 7-day sliding Cadence session). The file is
written 0600 where the OS honours that. Treat it like a password: don't commit it, don't share the
folder. "Sign out" in Settings clears it.
"""

import json
import logging
import os
import sys

CONFIG_FILENAME = "cadence_config.txt"

DEFAULT_HOTKEYS = {
    "play_pause": "ctrl+alt+up",
    "next_track": "ctrl+alt+right",
    "previous_track": "ctrl+alt+left",
    "like_unlike": "ctrl+alt+l",
    "show_current": "ctrl+alt+c",
}

# hotkey/config action id -> the controller method it calls. Both backends implement all five
# (cadence.CadenceController, spotify_backend.SpotifyController), and both front ends — the tray app and
# cli.py — bind through this one map so a renamed action can't half-work.
ACTIONS = {
    "play_pause": "play_pause",
    "next_track": "next_track",
    "previous_track": "previous_track",
    "like_unlike": "toggle_like",
    "show_current": "show_current",
}

# Which service the hotkeys drive.
#   "cadence" — a Cadence account (self-hosted player; commands reach the open browser tab). No Spotify
#               developer app and no credentials to bake in, so one exe works anywhere: the portable mode.
#   "spotify" — the original path: this machine's Spotify Connect device via the Web API, which needs a
#               Spotify app's Client ID + Secret (asked for on first launch, stored in this same file).
MODES = ("cadence", "spotify")
# There is deliberately NO default Cadence URL anywhere in the code: the app ships pointing at nobody's
# server. `cadence_url` starts empty, the user types their own address on the sign-in screen, and it
# lives in cadence_config.txt from then on. An empty URL fails loudly (cadence.CadenceClient) rather
# than quietly calling someone else's instance.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"

DEFAULTS = {
    "mode": "cadence",
    "cadence_url": "",
    # The signed Cadence session cookie. Saved so the login window appears once per machine, not once
    # per launch — each hotkey slides its 7-day window, so an agent in use never has to log in again.
    "cadence_session": "",
    # Cloudflare Access service token, for a Cadence host that sits behind Access. A native app can't
    # complete the interactive Access login, so this is the supported headless path.
    "cf_access_client_id": "",
    "cf_access_client_secret": "",
    # Spotify mode only — the developer app this machine controls Spotify Connect with.
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_redirect_uri": DEFAULT_REDIRECT_URI,
    # The OAuth refresh token, once the browser consent has happened. Kept here with everything else
    # rather than in a separate AppData cache file: "one file is the whole install" is the point of
    # this module, and a portable copy that carried its Cadence session but not its Spotify one was
    # asking to be re-authorised on every new machine.
    "spotify_refresh_token": "",
    "hotkeys": dict(DEFAULT_HOTKEYS),
}


def app_dir():
    """The folder the app runs from — where cadence_config.txt belongs.

    Frozen: the folder holding the exe, so a portable copy carries its settings with it (USB stick,
    Downloads folder, wherever it was dropped). Dev run: the folder holding this source file, which is
    the same idea and beats the current working directory (that changes with how you launched it).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_icon(name="poulet.ico"):
    """The app icon: beside the exe first (so a portable copy can swap it), then inside the PyInstaller
    bundle, then the source folder for a dev run. None when there is none — every caller has a fallback.

    Lives here, with the other path logic, because main.py and settings_ui.py each had their own copy
    and they had already drifted apart.
    """
    for base in (app_dir(), getattr(sys, "_MEIPASS", "")):
        candidate = os.path.join(base, name) if base else ""
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def appdata_dir(*parts):
    """%LOCALAPPDATA%\\MusicAgent\\Music Agent ERFFFFF[\\...] — the fallback home for the config, the
    Spotify token cache and the log.

    Spelled out rather than pulled from `appdirs`, which was a whole dependency (unmaintained since
    2020) for this one join. The path is byte-for-byte what appdirs.user_data_dir("Music Agent
    ERFFFFF", "MusicAgent") returned, so an existing install still finds everything it left here.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "MusicAgent", "Music Agent ERFFFFF", *parts)


def data_dir():
    """Where the config actually lands: next to the app, unless that folder is read-only (an installed
    copy under Program Files), in which case AppData — writing is not optional, so there is a fallback."""
    here = app_dir()
    if os.access(here, os.W_OK):
        return here
    path = appdata_dir()
    os.makedirs(path, exist_ok=True)
    return path


def config_path():
    return os.path.join(data_dir(), CONFIG_FILENAME)


def legacy_paths():
    """The pre-consolidation files, still read once so an existing install keeps its settings."""
    old = appdata_dir()
    return {
        "config": [os.path.join(data_dir(), "config.json"), os.path.join(old, "config.json")],
        "session": [os.path.join(data_dir(), "cadence_session.json"),
                    os.path.join(old, "cadence_session.json")],
        "env": [os.path.join(data_dir(), ".env"), os.path.join(app_dir(), ".env"),
                os.path.join(old, ".env")],
    }


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_env(path):
    """Minimal KEY=VALUE reader for a legacy .env — no dotenv dependency in the config layer."""
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    values[k.strip()] = v.strip()
    except OSError:
        pass
    return values


def _migrate():
    """Pull the old three files into one dict. Returns {} when there's nothing to migrate."""
    paths = legacy_paths()
    merged = {}
    for path in paths["config"]:
        if os.path.isfile(path):
            old = _read_json(path)
            merged.update({k: v for k, v in old.items() if k in DEFAULTS})
            break
    for path in paths["session"]:
        if os.path.isfile(path):
            cookie = _read_json(path).get("session")
            if cookie:
                merged["cadence_session"] = cookie
            break
    for path in paths["env"]:
        if os.path.isfile(path):
            env = _read_env(path)
            if env.get("SPOTIFY_CLIENT_ID"):
                merged["spotify_client_id"] = env.get("SPOTIFY_CLIENT_ID", "")
                merged["spotify_client_secret"] = env.get("SPOTIFY_CLIENT_SECRET", "")
                merged["spotify_redirect_uri"] = env.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)
            break
    if merged:
        logging.info("Migrated existing settings into %s", config_path())
    return merged


def load_config():
    """The whole configuration, defaults filled in. A missing or corrupt file yields defaults and never
    raises: this runs before anything else, so a bad file must not be able to stop the app starting."""
    path = config_path()
    if os.path.isfile(path):
        data = _read_json(path)
        if not data:
            logging.warning("%s is unreadable or not valid JSON — using defaults.", path)
    else:
        data = _migrate()
    # One merge for both branches. Hotkeys are merged key-by-key rather than replaced, so a config
    # (or a migrated legacy one) that binds a single action keeps the defaults for the other four.
    # `hotkeys` is only trusted when it's actually a dict — a hand-edited string or list would other-
    # wise raise out of the ** unpack, and this function must never stop the app from starting.
    hotkeys = data.get("hotkeys")
    if not isinstance(hotkeys, dict):
        hotkeys = {}
    cfg = {**DEFAULTS,
           **{k: v for k, v in data.items() if k in DEFAULTS and k != "hotkeys"},
           "hotkeys": {**DEFAULT_HOTKEYS,
                       **{k: v for k, v in hotkeys.items() if isinstance(v, str)}}}
    if cfg.get("mode") not in MODES:
        cfg["mode"] = DEFAULTS["mode"]
    return cfg


def save_config(config):
    """Write the whole config atomically (temp file + replace), 0600 where the OS honours it — the
    Cadence session token in here is a credential."""
    path = config_path()
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({k: config.get(k, v) for k, v in DEFAULTS.items()}, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows ACLs — nothing to do
        logging.info(f"Config saved to {path}")
    except OSError as e:
        logging.error(f"Failed to save config: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def update_config(key, value, cfg=None):
    """Write ONE field without clobbering the rest of the file.

    Long-lived objects hold the config dict they were built with and persist a credential minutes or
    hours later — a CadenceClient re-saving its sliding cookie, a Spotify _Auth storing a rotated
    refresh token. Writing that captured dict back would silently revert whatever the Settings window
    saved in between (measured: one cookie rotation put an old hotkey back on disk). So re-read, change
    the one field, write that. `cfg` is updated too so the caller's copy doesn't go stale.
    """
    latest = load_config()
    latest[key] = value
    save_config(latest)
    if cfg is not None:
        cfg[key] = value
    return latest


def is_configured(cfg):
    """Does the selected mode have what it needs to run? Reads the dict only — no network, no extra
    files — because this decides whether to throw a setup window (or a setup prompt) at the user on
    launch. Lives here rather than in main.py so the CLI can ask the same question without importing
    a GUI toolkit to do it.
    """
    if cfg["mode"] == "cadence":
        return bool(cfg.get("cadence_session"))
    return bool(cfg.get("spotify_client_id"))


def save_spotify_credentials(cfg, client_id, client_secret, redirect_uri=DEFAULT_REDIRECT_URI):
    """Store the Spotify app's keys (what the installer used to write into a .env).

    Changing the app invalidates the refresh token that was issued by the old one, so drop it here
    rather than letting the next hotkey fail with "Spotify rejected the sign-in".
    """
    client_id = client_id.strip()
    if client_id != cfg.get("spotify_client_id"):
        cfg["spotify_refresh_token"] = ""
    cfg["spotify_client_id"] = client_id
    cfg["spotify_client_secret"] = client_secret.strip()
    cfg["spotify_redirect_uri"] = (redirect_uri or DEFAULT_REDIRECT_URI).strip()
    save_config(cfg)
    return config_path()


def demo():
    """Self-check: paths, round-trip, migration, and that a corrupt file can't stop the app."""
    import tempfile
    global app_dir
    real_app_dir = app_dir
    with tempfile.TemporaryDirectory() as d:
        app_dir = lambda: d  # noqa: E731 — pretend the app lives here
        assert config_path() == os.path.join(d, "cadence_config.txt"), config_path()

        cfg = load_config()
        assert cfg["mode"] == "cadence" and cfg["hotkeys"] == DEFAULT_HOTKEYS
        assert cfg["cadence_session"] == "" and cfg["spotify_client_id"] == ""

        # everything in one file, including the session token and the shortcuts
        cfg["cadence_session"] = "cookie-value"
        cfg["cf_access_client_id"] = "cf-id"
        cfg["cf_access_client_secret"] = "cf-secret"
        cfg["hotkeys"]["play_pause"] = "f1"
        save_spotify_credentials(cfg, "sp-id", "sp-secret")
        raw = open(config_path()).read()
        for expected in ("cookie-value", "cf-id", "cf-secret", "sp-id", "sp-secret", "f1"):
            assert expected in raw, f"{expected} missing from {CONFIG_FILENAME}"
        back = load_config()
        assert back["cadence_session"] == "cookie-value"
        assert back["cf_access_client_id"] == "cf-id" and back["cf_access_client_secret"] == "cf-secret"
        assert back["spotify_client_id"] == "sp-id" and back["spotify_redirect_uri"].endswith("/callback")
        assert back["hotkeys"]["play_pause"] == "f1"                             # overridden
        assert back["hotkeys"]["next_track"] == DEFAULT_HOTKEYS["next_track"]     # rest defaulted

        # a refresh token belongs to the app that issued it: swapping credentials must drop it
        back["spotify_refresh_token"] = "issued-to-sp-id"
        save_spotify_credentials(back, "sp-id", "sp-secret")          # same app, keep it
        assert load_config()["spotify_refresh_token"] == "issued-to-sp-id"
        save_spotify_credentials(back, "other-id", "other-secret")    # different app, drop it
        assert load_config()["spotify_refresh_token"] == ""
        if os.name != "nt":
            assert oct(os.stat(config_path()).st_mode)[-3:] == "600", "session token must not be world-readable"

        # a corrupt file falls back to defaults instead of crashing the launch
        open(config_path(), "w").write("{ not json")
        assert load_config()["hotkeys"] == DEFAULT_HOTKEYS

        save_config({**DEFAULTS, "mode": "nonsense"})
        assert load_config()["mode"] == "cadence", "an unknown mode must not be trusted"

    # migration: the old three files land in the new one, untouched afterwards
    with tempfile.TemporaryDirectory() as d:
        app_dir = lambda: d  # noqa: E731
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({"mode": "cadence", "cadence_url": "https://old.example",
                       "cf_access_client_id": "old-cf", "hotkeys": {"next_track": "f2"}}, f)
        with open(os.path.join(d, "cadence_session.json"), "w") as f:
            json.dump({"url": "https://old.example", "session": "old-cookie"}, f)
        with open(os.path.join(d, ".env"), "w") as f:
            f.write("SPOTIFY_CLIENT_ID=old-id\nSPOTIFY_CLIENT_SECRET=old-secret\n")
        cfg = load_config()
        assert cfg["cadence_url"] == "https://old.example" and cfg["cadence_session"] == "old-cookie"
        assert cfg["cf_access_client_id"] == "old-cf" and cfg["spotify_client_id"] == "old-id"
        assert cfg["hotkeys"]["next_track"] == "f2"
        assert cfg["hotkeys"]["play_pause"] == DEFAULT_HOTKEYS["play_pause"], \
            "migrating one bound hotkey must not drop the defaults for the other four"
        assert is_configured(cfg), "a migrated Cadence session counts as configured"
        assert not is_configured({**DEFAULTS, "mode": "spotify"})
        save_config(cfg)
        assert os.path.isfile(os.path.join(d, "config.json")), "migration must not delete the old files"
    app_dir = real_app_dir
    print("config self-check ok")


if __name__ == "__main__":
    demo()
