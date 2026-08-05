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

SECURITY: every credential in here (session cookie, Cadence URL, Spotify and Cloudflare keys) is
encrypted at rest with Windows DPAPI — see SECRET_FIELDS below. `mode` and `hotkeys` stay readable on
purpose. The file is also written 0600 where the OS honours that. "Sign out" in Settings clears the
session.
"""

import base64
import ctypes
import json
import logging
import os
import sys

import appdirs

CONFIG_FILENAME = "cadence_config.txt"

DEFAULT_HOTKEYS = {
    "play_pause": "ctrl+alt+up",
    "next_track": "ctrl+alt+right",
    "previous_track": "ctrl+alt+left",
    "like_unlike": "ctrl+alt+l",
    "show_current": "ctrl+alt+c",
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
    "hotkeys": dict(DEFAULT_HOTKEYS),
}

# --------------------------------------------------------------------- credentials at rest (DPAPI)
# These fields are encrypted in the file with Windows CryptProtectData, which keys the ciphertext to
# the Windows ACCOUNT that saved it: no password to type at launch, and no key shipped inside the exe
# (which would only be obfuscation — anyone holding the exe would hold the key).
#
# The price is that the secrets do NOT travel with a portable copy. Carry the exe to another PC or
# another Windows user and these fields won't decrypt, so the app treats them as empty and asks you to
# sign in once there. `mode` and `hotkeys` are deliberately left plaintext so a moved copy still keeps
# its shortcuts and doesn't look corrupt.
SECRET_FIELDS = ("cadence_url", "cadence_session", "cf_access_client_id", "cf_access_client_secret",
                 "spotify_client_id", "spotify_client_secret")
ENC_PREFIX = "enc:"
CRYPTPROTECT_UI_FORBIDDEN = 0x1  # this is a --noconsole tray app: never let DPAPI pop a dialog


class _Blob(ctypes.Structure):
    """DATA_BLOB. c_uint32 rather than wintypes.DWORD because importing ctypes.wintypes raises on
    Linux/macOS, and this module has to at least IMPORT there (cadence.py's self-check runs anywhere)."""

    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(protect, data):
    """CryptProtectData / CryptUnprotectData over bytes. None means "couldn't": not Windows, or a blob
    that belongs to a different Windows account."""
    if os.name != "nt":
        return None
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _encrypt(value):
    """Plaintext -> "enc:<base64 DPAPI blob>". Empty stays empty (an encrypted "" is just noise), and
    an already-encrypted value passes through so a re-save can't double-wrap it.

    If DPAPI is unavailable the value is stored as-is: a config that refuses to save would lose the
    session the user just typed, which is worse than one that isn't encrypted on a platform that
    can't. On Windows this branch doesn't happen."""
    if not isinstance(value, str) or not value or value.startswith(ENC_PREFIX):
        return value
    blob = _dpapi(True, value.encode("utf-8"))
    if blob is None:
        logging.warning("DPAPI unavailable — credentials in %s are NOT encrypted.", CONFIG_FILENAME)
        return value
    return ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def _decrypt(value):
    """"enc:<base64>" -> plaintext. A value without the prefix is from a config written before this
    existed, and is returned unchanged (load_config re-saves it encrypted).

    A blob this account can't open yields "" rather than a raw error: is_configured() then reads it as
    "not signed in" and shows the sign-in window, which is the recoverable outcome. Handing the rest of
    the app an undecryptable string would instead look like a dead session token."""
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value
    try:
        blob = base64.b64decode(value[len(ENC_PREFIX):], validate=True)
    except ValueError:  # binascii.Error subclasses it
        return ""
    plain = _dpapi(False, blob)
    if plain is None:
        logging.warning("Saved credentials belong to a different Windows account — sign in again.")
        return ""
    return plain.decode("utf-8", "replace")


def app_dir():
    """The folder the app runs from — where cadence_config.txt belongs.

    Frozen: the folder holding the exe, so a portable copy carries its settings with it (USB stick,
    Downloads folder, wherever it was dropped). Dev run: the folder holding this source file, which is
    the same idea and beats the current working directory (that changes with how you launched it).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """Where the config actually lands: next to the app, unless that folder is read-only (an installed
    copy under Program Files), in which case AppData — writing is not optional, so there is a fallback."""
    here = app_dir()
    if os.access(here, os.W_OK):
        return here
    path = appdirs.user_data_dir("Music Agent ERFFFFF", "MusicAgent")
    os.makedirs(path, exist_ok=True)
    return path


def config_path():
    return os.path.join(data_dir(), CONFIG_FILENAME)


def legacy_paths():
    """The pre-consolidation files, still read once so an existing install keeps its settings."""
    old = appdirs.user_data_dir("Music Agent ERFFFFF", "MusicAgent")
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
    cfg = {**DEFAULTS, "hotkeys": dict(DEFAULT_HOTKEYS)}
    path = config_path()
    plaintext_on_disk = False
    if os.path.isfile(path):
        data = _read_json(path)
        if not data:
            logging.warning("%s is unreadable or not valid JSON — using defaults.", path)
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS and k != "hotkeys"})
        cfg["hotkeys"] = {**DEFAULT_HOTKEYS, **(data.get("hotkeys") or {})}
        # A file from before encryption existed, or written where DPAPI wasn't available.
        plaintext_on_disk = any(isinstance(data.get(k), str) and data[k]
                                and not data[k].startswith(ENC_PREFIX) for k in SECRET_FIELDS)
    else:
        cfg.update(_migrate())
        plaintext_on_disk = any(cfg.get(k) for k in SECRET_FIELDS)  # migrated in from the old files
    for key in SECRET_FIELDS:
        cfg[key] = _decrypt(cfg.get(key, ""))
    if cfg.get("mode") not in MODES:
        cfg["mode"] = DEFAULTS["mode"]
    if plaintext_on_disk:
        # Encrypt on sight instead of waiting for the next Settings save — otherwise an upgraded
        # install leaves its old plaintext session sitting on disk until the user happens to save.
        try:
            save_config(cfg)
        except OSError:
            pass  # a read-only config dir is already handled everywhere else; don't break the launch
    return cfg


def save_config(config):
    """Write the whole config atomically (temp file + replace), 0600 where the OS honours it. Every
    field in SECRET_FIELDS is DPAPI-encrypted on the way out — `config` itself stays plaintext, since
    that's the dict the rest of the app reads from."""
    path = config_path()
    tmp_path = path + ".tmp"
    try:
        stored = {k: config.get(k, v) for k, v in DEFAULTS.items()}
        for key in SECRET_FIELDS:
            stored[key] = _encrypt(stored.get(key, ""))
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)
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


def spotify_credentials(cfg):
    """Spotify keys in the SPOTIFY_* shape spotipy expects, from the config file."""
    return {
        "SPOTIFY_CLIENT_ID": cfg.get("spotify_client_id", ""),
        "SPOTIFY_CLIENT_SECRET": cfg.get("spotify_client_secret", ""),
        "SPOTIFY_REDIRECT_URI": cfg.get("spotify_redirect_uri") or DEFAULT_REDIRECT_URI,
    }


def save_spotify_credentials(cfg, client_id, client_secret, redirect_uri=DEFAULT_REDIRECT_URI):
    """Store the Spotify app's keys (what the installer used to write into a .env)."""
    cfg["spotify_client_id"] = client_id.strip()
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
        cfg["cadence_url"] = "https://cadence.example"
        cfg["cadence_session"] = "cookie-value"
        cfg["cf_access_client_id"] = "cf-id"
        cfg["cf_access_client_secret"] = "cf-secret"
        cfg["hotkeys"]["play_pause"] = "f1"
        save_spotify_credentials(cfg, "sp-id", "sp-secret")

        # ...but no credential is readable in it
        raw = open(config_path()).read()
        assert "f1" in raw and '"mode": "cadence"' in raw, "mode and hotkeys stay plaintext on purpose"
        if os.name == "nt":
            for secret in ("cookie-value", "cf-id", "cf-secret", "sp-id", "sp-secret", "cadence.example"):
                assert secret not in raw, f"{secret} is sitting in {CONFIG_FILENAME} IN PLAINTEXT"
            assert raw.count(ENC_PREFIX) == len(SECRET_FIELDS), raw
        back = load_config()
        assert back["cadence_url"] == "https://cadence.example"
        assert back["cadence_session"] == "cookie-value"
        assert back["cf_access_client_id"] == "cf-id" and back["cf_access_client_secret"] == "cf-secret"
        assert back["spotify_client_id"] == "sp-id" and back["spotify_redirect_uri"].endswith("/callback")
        assert back["hotkeys"]["play_pause"] == "f1"                             # overridden
        assert back["hotkeys"]["next_track"] == DEFAULT_HOTKEYS["next_track"]     # rest defaulted
        assert spotify_credentials(back)["SPOTIFY_CLIENT_ID"] == "sp-id"
        if os.name != "nt":
            assert oct(os.stat(config_path()).st_mode)[-3:] == "600", "session token must not be world-readable"

        # a blob from another Windows account reads as "not signed in", not as a broken token
        if os.name == "nt":
            with open(config_path()) as f:
                data = json.load(f)
            data["cadence_session"] = ENC_PREFIX + base64.b64encode(b"not my blob").decode()
            with open(config_path(), "w") as f:
                json.dump(data, f)
            assert load_config()["cadence_session"] == "", "an unopenable blob must not reach the app"

        # an existing plaintext config is encrypted on the next load, not left lying around
        with open(config_path(), "w") as f:
            json.dump({"mode": "cadence", "cadence_session": "old-plaintext"}, f)
        assert load_config()["cadence_session"] == "old-plaintext"
        if os.name == "nt":
            assert "old-plaintext" not in open(config_path()).read(), "upgrade must re-encrypt in place"

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
        save_config(cfg)
        assert os.path.isfile(os.path.join(d, "config.json")), "migration must not delete the old files"
    app_dir = real_app_dir
    print("config self-check ok")


if __name__ == "__main__":
    demo()
