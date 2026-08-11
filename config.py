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

It replaces the old config.json + cadence_session.json trio, which scattered one app's settings across
three files and two directories; those are migrated in on first run and then left alone.

**A `.env` beside the app overrides all of it** (see ENV_FIELDS). That is the file an operator owns and
edits by hand: server address, Cloudflare service token, Cadence account, Spotify app keys. Anything it
supplies is read fresh on every load and is *never written back* — `save_config` blanks those fields,
so a secret cannot end up duplicated in cadence_config.txt where it would have to be rotated twice.
What cadence_config.txt still owns is the things the app itself earns or the user picks in the UI: the
session cookie, the Spotify refresh token, the mode and the hotkeys.

SECURITY: every credential still stored here (session cookie, Cadence URL, Spotify and Cloudflare keys)
is encrypted at rest with Windows DPAPI — see SECRET_FIELDS below. `mode` and `hotkeys` stay readable
on purpose. The file is also written 0600 where the OS honours that. "Sign out" in Settings clears the
session.
"""

import base64
import ctypes
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
# music_agent_cli.py — bind through this one map so a renamed action can't half-work.
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
    # this module. It is a bearer credential, so it is in SECRET_FIELDS and travels no further than
    # the Windows account that saved it — same rule as the Cadence session.
    "spotify_refresh_token": "",
    "hotkeys": dict(DEFAULT_HOTKEYS),
}

# ------------------------------------------------------------------------ the operator's own .env
# The one file a human edits by hand, and the one place a secret has to live. `.env` keys on the left,
# config fields on the right; matching ignores case. Aliases are listed generic-first, specific-last,
# so a file carrying both DOMAIN and CADENCE_URL resolves to the unambiguous one.
#
# `cadence_username` / `cadence_password` are deliberately NOT in DEFAULTS: they exist only in this
# dict and in memory, so `save_config` (which is built from DEFAULTS) cannot write an account password
# to disk even by accident. The app stores the SESSION it exchanges them for, never the password.
ENV_FILENAME = ".env"

# The shortcuts, one .env key per action: HOTKEY_PLAY_PAUSE=ctrl+alt+up, and so on. Derived from
# ACTIONS rather than typed out, so adding an action cannot leave it unconfigurable — and so the key
# name is always predictable from the action id instead of being a second thing to look up.
#
# These are NOT in ENV_FIELDS: hotkeys are a nested dict, not a flat field, so env_config() merges
# them key-by-key (a .env that sets one shortcut must keep the defaults for the other four). Handled
# in env_config() below.
ENV_HOTKEY_PREFIX = "HOTKEY_"
ENV_HOTKEYS = {ENV_HOTKEY_PREFIX + action_id.upper(): action_id for action_id in ACTIONS}

ENV_FIELDS = {
    "MODE": "mode",
    "DOMAIN": "cadence_url",
    "CADENCE_URL": "cadence_url",
    "USERNAME": "cadence_username",
    "CADENCE_USERNAME": "cadence_username",
    "PASSWORD": "cadence_password",
    "CADENCE_PASSWORD": "cadence_password",
    "CF_ACCESS_CLIENT_ID": "cf_access_client_id",
    "CF_ACCESS_CLIENT_SECRET": "cf_access_client_secret",
    "SPOTIFY_CLIENT_ID": "spotify_client_id",
    "SPOTIFY_CLIENT_SECRET": "spotify_client_secret",
    "SPOTIFY_REDIRECT_URI": "spotify_redirect_uri",
}


# A corporate proxy is NOT a config field, and deliberately isn't in DEFAULTS: `urllib` reads it from
# the ENVIRONMENT (and, on Windows, the registry), and every opener httpmin builds already carries the
# ProxyHandler that does so — including CONNECT tunnelling for https and NO_PROXY bypass. So the whole
# job here is to bridge one file to another mechanism, not to plumb a value through two backends.
#
# This is the case it exists for: on a network with a mandatory proxy the client is not supposed to
# resolve external names at all — the proxy does — which is why the failure without one is
# `getaddrinfo failed` (WSA 11001) rather than a timeout, and why it looks like broken DNS.
ENV_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")

# What `PROXY_AUTH` may say to mean "use the logged-in Windows account". Several spellings because
# this is typed by hand into a file, once, on a machine where the thing it enables is the difference
# between the app working and not.
PROXY_AUTH_CURRENT_USER = ("current-user", "currentuser", "windows", "sspi", "negotiate", "ntlm")


def apply_env_proxy():
    """Export the `.env`'s proxy settings into the environment, where urllib will find them.

    `PROXY=` is the friendly form — one address for both schemes. `HTTP_PROXY` / `HTTPS_PROXY` /
    `NO_PROXY` are passed through untouched for anyone who needs them to differ. Returns what it set,
    so `status` can show it.

    The `.env` WINS over a variable already in the shell, same as every other setting it supplies —
    otherwise "what the .env supplies, the .env owns" would have one silent exception. A `.env` with
    no proxy line sets nothing, so a shell variable still works on its own.
    """
    path = env_path()
    raw = {k.upper(): v for k, v in _read_env(path).items()} if path else {}
    both = raw.get("PROXY", "")
    applied = {}
    for key in ENV_PROXY_KEYS:
        value = raw.get(key) or (both if key != "NO_PROXY" else "")
        if value:
            os.environ[key] = value
            applied[key] = value

    # PROXY_AUTH=current-user means "authenticate to the proxy as whoever is logged in", i.e. NTLM or
    # Negotiate over SSPI. urllib cannot do that at all — measured against proxies demanding each, it
    # never attempts them and just surfaces the 407 — so this switches the transport to Windows' own
    # HTTP stack, which does it (and reads a PAC file, which urllib also cannot).
    #
    # Imported here rather than at the top so config.py stays the module with no app dependencies,
    # and so a machine that never asks for this never loads either transport module.
    import httpmin

    wanted = raw.get("PROXY_AUTH", "").strip().lower().replace("_", "-") in PROXY_AUTH_CURRENT_USER
    # Always called, including with False: the default transport has to be restored if the setting is
    # removed, or a process that once saw it would keep the other one for its whole life.
    in_effect = httpmin.use_windows_transport(wanted)
    if wanted:
        applied["PROXY_AUTH"] = "current-user" if in_effect else "current-user (UNAVAILABLE: not Windows)"
    return applied


def normalize_url(raw):
    """Accept what people actually type. "cadence.example.com" is a URL to a human but not to an HTTP
    client, so assume https rather than failing with a connection error they can't act on.

    Lives in config, the module with no dependencies of its own, because every entry point needs it:
    the `.env` reader below, cadence.py, the CLI, and the sign-in window (which is behind
    `import customtkinter` and must not be what the others have to import to get this).
    """
    url = (raw or "").strip().rstrip("/")
    return "https://" + url if url and "://" not in url else url

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
                 "spotify_client_id", "spotify_client_secret", "spotify_refresh_token")
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
    """The pre-consolidation files, still read once so an existing install keeps its settings.

    `.env` is NOT in here any more: it is no longer a legacy format to absorb but a live input, read
    on every load by env_config(). Migrating it would have copied the operator's secrets into a second
    file they then had to remember to rotate.
    """
    old = appdata_dir()
    return {
        "config": [os.path.join(data_dir(), "config.json"), os.path.join(old, "config.json")],
        "session": [os.path.join(data_dir(), "cadence_session.json"),
                    os.path.join(old, "cadence_session.json")],
    }


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_env(path):
    """Minimal KEY=VALUE reader — no dotenv dependency in the config layer.

    Deliberately does NOT strip an inline `#` comment: a password is far more likely to contain one
    than a line is to carry a trailing note, and silently truncating a password produces a login
    failure nobody can explain. A whole-line `#` is still a comment. A value wrapped in one matching
    pair of quotes is unwrapped, since that is how people escape trailing spaces.
    """
    values = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip()
                if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key.strip()] = value
    except OSError:
        pass
    return values


def env_path():
    """The `.env` the app reads, or "" when there is none.

    Where the config already lives first (AppData for an installed copy under Program Files), then
    beside the app, so a portable copy carries its own. Two places, both of them "next to the app" in
    the sense the user means; nothing hunts through the home directory.
    """
    for base in (data_dir(), app_dir()):
        candidate = os.path.join(base, ENV_FILENAME)
        if os.path.isfile(candidate):
            return candidate
    return ""


def env_config():
    """Whatever the `.env` supplies, translated into config fields. `{}` when there is no file.

    Keys are matched case-insensitively — the file is hand-written, and `CF_Access_Client_Id` and
    `CF_ACCESS_CLIENT_ID` are obviously the same setting. Empty values are ignored rather than
    overriding a saved one with nothing, so a key left blank in the template is simply not set.
    """
    path = env_path()
    if not path:
        return {}
    raw = {k.upper(): v for k, v in _read_env(path).items()}
    found = {}
    for env_key, field in ENV_FIELDS.items():
        value = raw.get(env_key, "")
        if value:
            found[field] = value
    if found.get("cadence_url"):
        found["cadence_url"] = normalize_url(found["cadence_url"])
    if found.get("mode") not in MODES:
        found.pop("mode", None)
    # Shortcuts merge instead of replacing: a .env that names ONE of them must leave the other four
    # at whatever the config file (or the defaults) say, exactly like load_config's own hotkey merge.
    # Returned under "hotkeys" as a partial dict, which is what makes the caller's merge possible —
    # returning a complete one here would silently reset the unmentioned four.
    shortcuts = {action_id: raw[env_key] for env_key, action_id in ENV_HOTKEYS.items()
                 if raw.get(env_key)}
    if shortcuts:
        found["hotkeys"] = shortcuts
    return found


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
    if merged:
        logging.info("Migrated existing settings into %s", config_path())
    return merged


def load_config():
    """The whole configuration, defaults filled in. A missing or corrupt file yields defaults and never
    raises: this runs before anything else, so a bad file must not be able to stop the app starting."""
    path = config_path()
    plaintext_on_disk = False
    if os.path.isfile(path):
        data = _read_json(path)
        if not data:
            logging.warning("%s is unreadable or not valid JSON — using defaults.", path)
        # A file from before encryption existed, or written where DPAPI wasn't available.
        plaintext_on_disk = any(isinstance(data.get(k), str) and data[k]
                                and not data[k].startswith(ENC_PREFIX) for k in SECRET_FIELDS)
    else:
        data = _migrate()
        plaintext_on_disk = any(data.get(k) for k in SECRET_FIELDS)  # migrated in from the old files
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
    for key in SECRET_FIELDS:
        cfg[key] = _decrypt(cfg.get(key, ""))
    # The .env wins, and it wins LAST: it is the file the operator edits, so a value they just changed
    # there has to beat whatever an earlier run happened to leave in cadence_config.txt. It also adds
    # cadence_username/cadence_password, which have no saved counterpart by design.
    env = env_config()
    # Shortcuts merge action-by-action for the same reason the block above does: a .env naming one
    # HOTKEY_* must not blank the other four. `update` would have replaced the whole dict.
    cfg["hotkeys"].update(env.pop("hotkeys", {}))
    cfg.update(env)
    # Every entry point loads the config before it makes a request, so this is the one place that
    # guarantees the proxy is in effect for both front ends without either of them knowing about it.
    apply_env_proxy()
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
        # A credential the .env supplies stays in the .env. Without this every save would copy it into
        # cadence_config.txt as well, and rotating a leaked key would mean editing two files — with the
        # forgotten copy still working, because load_config would keep reading it whenever the operator
        # emptied the .env line instead of deleting it.
        # ONE rule, for every kind of setting: what the .env supplies, the .env KEEPS. The config file
        # stores that field's default instead. Two consequences, both wanted:
        #   - there is never a second copy of a credential to find and rotate;
        #   - deleting a line from the .env returns that setting to its default, rather than
        #     resurrecting whatever value happened to be saved under it before.
        # `cadence_username` / `cadence_password` need no case here: they are not in DEFAULTS, so
        # `stored` never had them.
        for key, value in env_config().items():
            if key == "hotkeys":
                stored["hotkeys"] = {**stored["hotkeys"],
                                     **{action: DEFAULT_HOTKEYS[action] for action in value}}
            elif key in DEFAULTS:
                stored[key] = DEFAULTS[key]
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

    A saved session is one way to be ready; a `.env` carrying the server and the account is the other,
    since the app can exchange those for a session with nobody watching. Without that second clause a
    fully-provisioned `.env` still opened a sign-in window on first launch.
    """
    if cfg["mode"] == "cadence":
        return bool(cfg.get("cadence_session") or (cfg.get("cadence_url")
                    and cfg.get("cadence_username") and cfg.get("cadence_password")))
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
            # Every secret that HAS a value is sealed. Counting SECRET_FIELDS instead was wrong from
            # the day spotify_refresh_token joined them: _encrypt leaves "" as "", so an unset field
            # contributes no prefix and this failed on a config that was perfectly correct.
            assert raw.count(ENC_PREFIX) == sum(1 for k in SECRET_FIELDS if cfg.get(k)), raw
        back = load_config()
        assert back["cadence_url"] == "https://cadence.example"
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

    # migration: the old two files land in the new one, untouched afterwards
    with tempfile.TemporaryDirectory() as d:
        app_dir = lambda: d  # noqa: E731
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({"mode": "cadence", "cadence_url": "https://old.example",
                       "cf_access_client_id": "old-cf", "hotkeys": {"next_track": "f2"}}, f)
        with open(os.path.join(d, "cadence_session.json"), "w") as f:
            json.dump({"url": "https://old.example", "session": "old-cookie"}, f)
        cfg = load_config()
        assert cfg["cadence_url"] == "https://old.example" and cfg["cadence_session"] == "old-cookie"
        assert cfg["cf_access_client_id"] == "old-cf"
        assert cfg["hotkeys"]["next_track"] == "f2"
        assert cfg["hotkeys"]["play_pause"] == DEFAULT_HOTKEYS["play_pause"], \
            "migrating one bound hotkey must not drop the defaults for the other four"
        assert is_configured(cfg), "a migrated Cadence session counts as configured"
        assert not is_configured({**DEFAULTS, "mode": "spotify"})
        save_config(cfg)
        assert os.path.isfile(os.path.join(d, "config.json")), "migration must not delete the old files"

    # ------------------------------------------------------------------ the .env is the source of truth
    with tempfile.TemporaryDirectory() as d:
        app_dir = lambda: d  # noqa: E731
        assert env_path() == "" and env_config() == {}, "no .env is a normal state, not an error"

        with open(os.path.join(d, ENV_FILENAME), "w", encoding="utf-8") as f:
            f.write("# a comment\n"
                    "\n"
                    "DOMAIN=music.example.com\n"                 # bare host: must gain https
                    "USERNAME=someone\n"
                    "PASSWORD=p#ss w+rd$with=signs\n"            # '#' is NOT an inline comment here
                    'SPOTIFY_CLIENT_ID="quoted-id"\n'            # one matching pair is unwrapped
                    "SPOTIFY_CLIENT_SECRET=\n"                   # blank: ignored, not "set to empty"
                    "CF_Access_Client_Id=cf-id.access\n"         # the case people actually type
                    "CF_ACCESS_CLIENT_SECRET=cf-secret\n")
        cfg = load_config()
        assert cfg["cadence_url"] == "https://music.example.com"
        assert cfg["cadence_username"] == "someone"
        assert cfg["cadence_password"] == "p#ss w+rd$with=signs", cfg["cadence_password"]
        assert cfg["spotify_client_id"] == "quoted-id"
        assert cfg["cf_access_client_id"] == "cf-id.access" and cfg["cf_access_client_secret"] == "cf-secret"
        assert cfg["spotify_client_secret"] == "", "a blank line must not be read as a value"
        assert is_configured(cfg), "server + account in the .env is enough to run unattended"

        # ...and none of it is copied into cadence_config.txt, encrypted or otherwise. One secret,
        # one file, one thing to rotate.
        cfg["cadence_session"] = "earned-cookie"
        cfg["hotkeys"]["play_pause"] = "f7"
        save_config(cfg)
        stored = json.load(open(config_path()))
        for field in ("cadence_url", "cadence_username", "cadence_password", "spotify_client_id",
                      "cf_access_client_id", "cf_access_client_secret"):
            assert not stored.get(field), f"{field} was copied out of the .env into the config file"
        assert "someone" not in open(config_path()).read()
        assert stored["cadence_session"] and stored["hotkeys"]["play_pause"] == "f7", \
            "what the app earns itself still belongs in the config file"

        # the .env still wins on the way back in, and an edit to it takes effect with no re-save
        assert load_config()["cadence_url"] == "https://music.example.com"
        with open(os.path.join(d, ENV_FILENAME), "a", encoding="utf-8") as f:
            f.write("CADENCE_URL=https://moved.example\nMODE=spotify\n")
        moved = load_config()
        assert moved["cadence_url"] == "https://moved.example", "CADENCE_URL must beat DOMAIN"
        assert moved["mode"] == "spotify" and moved["cadence_session"] == "earned-cookie"

        # ...and `mode` obeys the same rule as everything else the .env supplies: not persisted, so
        # there is no stale second copy sitting in the config file claiming otherwise.
        save_config(moved)
        assert json.load(open(config_path()))["mode"] == DEFAULTS["mode"], "an .env mode was persisted"
        assert load_config()["mode"] == "spotify", "...but the .env still drives it"

    # ------------------------------------------------------------------ shortcuts from the .env
    with tempfile.TemporaryDirectory() as d:
        app_dir = lambda: d  # noqa: E731
        cfg = load_config()
        cfg["hotkeys"]["next_track"] = "f2"          # a shortcut the CONFIG FILE owns
        cfg["hotkeys"]["like_unlike"] = "f3"
        save_config(cfg)

        with open(os.path.join(d, ENV_FILENAME), "w", encoding="utf-8") as f:
            f.write("HOTKEY_PLAY_PAUSE=ctrl+shift+p\nhotkey_like_unlike=ctrl+shift+k\n")  # case: free
        back = load_config()
        assert back["hotkeys"]["play_pause"] == "ctrl+shift+p", "the .env must set a shortcut"
        assert back["hotkeys"]["like_unlike"] == "ctrl+shift+k", "...and beat the saved one"
        assert back["hotkeys"]["next_track"] == "f2", "naming one shortcut must not reset the others"
        assert back["hotkeys"]["show_current"] == DEFAULT_HOTKEYS["show_current"]

        # ...and what the .env owns never lands in the config file, so deleting the line restores the
        # default rather than resurrecting whatever was last saved under it
        save_config(back)
        stored = json.load(open(config_path()))["hotkeys"]
        assert stored["play_pause"] == DEFAULT_HOTKEYS["play_pause"], stored
        assert stored["like_unlike"] == DEFAULT_HOTKEYS["like_unlike"], stored
        assert stored["next_track"] == "f2", "a shortcut the config file owns must survive a save"
        os.remove(os.path.join(d, ENV_FILENAME))
        gone = load_config()
        assert gone["hotkeys"]["play_pause"] == DEFAULT_HOTKEYS["play_pause"]
        assert gone["hotkeys"]["like_unlike"] == DEFAULT_HOTKEYS["like_unlike"]
        assert gone["hotkeys"]["next_track"] == "f2"

        # every action has to be reachable from the .env, or one of them is silently unconfigurable
        assert set(ENV_HOTKEYS.values()) == set(ACTIONS), ENV_HOTKEYS

    # ------------------------------------------------------- proxy, for a network that mandates one
    with tempfile.TemporaryDirectory() as d:
        app_dir = lambda: d  # noqa: E731
        saved = {key: os.environ.get(key) for key in ENV_PROXY_KEYS}
        try:
            for key in ENV_PROXY_KEYS:
                os.environ.pop(key, None)
            assert apply_env_proxy() == {}, "no .env is a normal state here too"

            with open(os.path.join(d, ENV_FILENAME), "w") as f:
                f.write("PROXY=http://corp:8080\n")
            load_config()                       # every entry point goes through this, so it must apply
            assert os.environ["HTTP_PROXY"] == "http://corp:8080"
            assert os.environ["HTTPS_PROXY"] == "http://corp:8080", "PROXY has to cover both schemes"
            assert "NO_PROXY" not in os.environ, "PROXY is an address, not a bypass list"

            # the explicit names beat the shorthand, and NO_PROXY passes through untouched
            with open(os.path.join(d, ENV_FILENAME), "w") as f:
                f.write("PROXY=http://both:8080\nHTTPS_PROXY=http://secure:8443\nNO_PROXY=localhost\n")
            load_config()
            assert os.environ["HTTPS_PROXY"] == "http://secure:8443"
            assert os.environ["HTTP_PROXY"] == "http://both:8080"
            assert os.environ["NO_PROXY"] == "localhost"

            # ...and urllib really reads what was exported. That is the ONLY reason this works: the
            # proxy is never passed to httpmin, it is picked up by the ProxyHandler in every opener.
            import urllib.request
            assert urllib.request.getproxies().get("https") == "http://secure:8443"
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
    app_dir = real_app_dir
    print("config self-check ok")


if __name__ == "__main__":
    demo()
