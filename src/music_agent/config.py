"""Everything the app remembers, in ONE file: cadence_config.txt, next to the app itself.

Format is JSON (readable, and one parser instead of three) holding the whole configuration:

    {
      "mode": "cadence",
      "cadence_url": "https://cadence.example.com",   # YOUR server — no default is baked in
      "cadence_session": "<signed session cookie — this is a credential>",
      "cadence_username": "", "cadence_password": "",   # what the sign-in window collects
      "cf_access_client_id": "", "cf_access_client_secret": "",
      "spotify_client_id": "", "spotify_client_secret": "", "spotify_redirect_uri": "...",
      "proxy_url": "", "proxy_user": "", "proxy_password": "", "proxy_auth": "",
      "hotkeys": {"play_pause": "ctrl+alt+up", ...}
    }

    Every value above except `mode`, `hotkeys`, `spotify_redirect_uri`, `proxy_url` and `proxy_auth`
    is stored as `"enc:<base64 DPAPI blob>"`, not as what you see here. The KEYS stay readable — the
    file has to remain JSON someone can look at — but no value you would mind losing is in the clear.

It replaces the old config.json + cadence_session.json trio, which scattered one app's settings across
three files and two directories; those are migrated in on first run and then left alone.

**In the CLI — and only the CLI — a `.env` beside the app overrides all of it** (see ENV_FIELDS and
`use_env`). That is the file an operator owns and edits by hand: server address, Cloudflare service
token, Cadence account, Spotify app keys. Anything it supplies is read fresh on every load and is
*never written back* — `save_config` blanks those fields, so a secret cannot end up duplicated in
cadence_config.txt where it would have to be rotated twice.

**The tray app never reads it.** Portable or installed, the GUI is configured from cadence_config.txt
and from its own windows: one file, one place, and Settings means what it says. A .env is a headless
mechanism for a headless front end.

SECURITY: every credential stored here — session cookie, Cadence URL, **account and password**,
Spotify and Cloudflare keys, and the **proxy account** — is encrypted at rest with Windows DPAPI, see
SECRET_FIELDS below. `mode` and `hotkeys` stay readable on purpose. The file is also written 0600
where the OS honours that. "Sign out" in Settings clears the session AND the stored account, or it
would silently sign itself back in on the next launch.
"""

import base64
import ctypes
import json
import logging
import os
import sys
import urllib.parse
import urllib.request

CONFIG_FILENAME = "cadence_config.txt"

DEFAULT_HOTKEYS = {
    "play_pause": "ctrl+alt+up",
    "next_track": "ctrl+alt+right",
    "previous_track": "ctrl+alt+left",
    "like_unlike": "ctrl+alt+l",
    "show_current": "ctrl+alt+c",
}

# hotkey/config action id -> the controller method it calls. Both backends implement all five
# (cadence.CadenceController, spotify.SpotifyController), and both front ends — the tray app and
# music_agent/cli.py — bind through this one map so a renamed action can't half-work.
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
    # The account itself. Stored (sealed, like everything else in SECRET_FIELDS) so the sign-in window
    # can show you what it already has instead of an empty form, and so an expired session re-signs in
    # without a window at all. The CLI has always had this from its .env; the GUI now has its own copy
    # of the same thing, which is what "the app is configured from its config file" has to mean.
    #
    # It is a real widening of what sits on disk — a password is reusable where a session cookie is
    # not — and it is deliberate: the alternative is an app that asks for a password it will not
    # remember. Sign out clears both, DPAPI seals both, and neither is ever written when the .env
    # supplies it.
    "cadence_username": "",
    "cadence_password": "",
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
    # Corporate proxy. All optional: empty means "connect directly", which is what almost every
    # machine wants. Kept as ordinary config fields rather than environment-only so the Settings
    # window can offer them — the .env still overrides, like everything else.
    "proxy_url": "",            # host:port, or a full http://host:port
    "proxy_user": "",
    "proxy_password": "",
    "proxy_auth": "",           # "current-user" to authenticate as the logged-in Windows account
    "hotkeys": dict(DEFAULT_HOTKEYS),
}

# ------------------------------------------------------------------------ the operator's own .env
# The one file a human edits by hand, and the one place a secret has to live. `.env` keys on the left,
# config fields on the right; matching ignores case. Aliases are listed generic-first, specific-last,
# so a file carrying both DOMAIN and CADENCE_URL resolves to the unambiguous one.
#
# `cadence_username` / `cadence_password` ARE in DEFAULTS now, because the sign-in window has to be
# able to show you what it already knows. That does not weaken the rule below: a value the .env
# supplies is still written back as its DEFAULT, so an account configured in a .env never lands in
# cadence_config.txt — only one typed into the window does.
ENV_FILENAME = ".env"

# ------------------------------------------------------------------------------ who may read a .env
# The CLI, and nothing else.
#
# The tray app — portable or installed — is configured from cadence_config.txt through its own
# windows, and a file it never shows you must not be able to overrule what you typed into Settings.
# That is what a .env did: it silently won, so the greyed-out boxes and the ".env" tags in Settings
# existed only to explain why the app was ignoring you. Deleting the override deletes all of that.
#
# Off by DEFAULT, and `cli.main()` is the one caller that turns it on. Fail closed: a front end
# added later reads the config file until someone deliberately opts it in, which is the safe way for
# this to be wrong. Every .env reader in this module goes through `env_path()`, so this one gate
# covers env_config(), apply_proxy(), save_config()'s "the .env keeps it" rule and all the UI text.
_env_enabled = False


def use_env(enabled=True):
    """Let this process read a `.env`. Called by the CLI at startup; nothing else calls it."""
    global _env_enabled
    _env_enabled = bool(enabled)
    return _env_enabled

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
    "PROXY": "proxy_url",
    "PROXY_URL": "proxy_url",
    "PROXY_USER": "proxy_user",
    "PROXY_USERNAME": "proxy_user",
    "PROXY_PASSWORD": "proxy_password",
    "PROXY_PASS": "proxy_password",
    "PROXY_AUTH": "proxy_auth",
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
# What apply_proxy() itself exported last time, so it can clear its own and only its own.
_exported_proxy_keys = set()

# What `PROXY_AUTH` may say to mean "use the logged-in Windows account". Several spellings because
# this is typed by hand into a file, once, on a machine where the thing it enables is the difference
# between the app working and not.
PROXY_AUTH_CURRENT_USER = ("current-user", "currentuser", "windows", "sspi", "negotiate", "ntlm")


def proxy_url(cfg):
    """The full proxy address, credentials folded in, or "" when no proxy is configured.

    `proxy_user` / `proxy_password` are kept as separate fields rather than as `user:pass@host` so
    the Settings window can have three ordinary boxes and so the password can be sealed on its own.
    They are quoted with `safe=""` on the way in: a proxy password containing `@`, `:` or `/` would
    otherwise split the URL somewhere other than where it means to, and the failure looks like a
    wrong password rather than a parsing bug.
    """
    address = (cfg.get("proxy_url") or "").strip()
    if not address:
        return ""
    if "://" not in address:
        address = "http://" + address
    user, password = (cfg.get("proxy_user") or "").strip(), cfg.get("proxy_password") or ""
    if not user:
        return address
    scheme, _, rest = address.partition("://")
    rest = rest.partition("@")[2] or rest          # never stack credentials onto an address that has some
    quote = urllib.parse.quote
    secret = f":{quote(password, safe='')}" if password else ""
    return f"{scheme}://{quote(user, safe='')}{secret}@{rest}"


def apply_proxy(cfg):
    """Put the configured proxy where urllib will find it — the environment — and pick the transport.

    ONE function for both front ends: `cfg` has already had the .env overlaid, so a proxy typed into
    Settings and one written into the .env arrive here identically, with the .env winning as usual.

    Returns what it set, for `status` and the Settings window to display.
    """
    global _exported_proxy_keys
    path = env_path()
    raw = {k.upper(): v for k, v in _read_env(path).items()} if path else {}
    composed = proxy_url(cfg)
    applied = {}
    for key in ENV_PROXY_KEYS:
        # HTTP_PROXY/HTTPS_PROXY/NO_PROXY straight from the .env are for the rare case where the two
        # schemes must differ; otherwise the one composed address covers both.
        value = raw.get(key) or (composed if key != "NO_PROXY" else "")
        if value:
            os.environ[key] = value
            applied[key] = value
        elif key in _exported_proxy_keys:
            # Clear ONLY what a previous call here set. Emptying the proxy in Settings has to really
            # stop using it — but a variable the user exported in their own shell is not ours to
            # delete, and doing so broke "no proxy configured, so my shell setting still applies".
            os.environ.pop(key, None)
    _exported_proxy_keys = set(applied)

    # proxy_auth=current-user means "authenticate to the proxy as whoever is logged in", i.e. NTLM or
    # Negotiate over SSPI. urllib cannot do that at all — measured against proxies demanding each, it
    # never attempts them and just surfaces the 407 — so this switches the transport to Windows' own
    # HTTP stack, which does it (and reads a PAC file, which urllib also cannot).
    #
    # Imported here rather than at the top so config.py stays the module with no app dependencies,
    # and so a machine that never asks for this never loads either transport module.
    from music_agent.net import httpmin

    wanted = (cfg.get("proxy_auth") or "").strip().lower().replace("_", "-") in PROXY_AUTH_CURRENT_USER
    # Always called, including with False: the default transport has to be restored if the setting is
    # removed, or a process that once saw it would keep the other one for its whole life.
    in_effect = httpmin.use_windows_transport(wanted)
    if wanted:
        applied["PROXY_AUTH"] = "current-user" if in_effect else "current-user (UNAVAILABLE: not Windows)"
    if cfg.get("proxy_password") and not (cfg.get("proxy_user") or "").strip():
        # proxy_url() drops the password when there is no username to attach it to, and a silently
        # ignored credential looks exactly like a wrong one. Here rather than in each dialog: the
        # CLI reaches this too, and one warning cannot fall out of step with itself.
        logging.warning("A proxy password is set with no proxy username — it will not be sent.")
    logging.debug("Proxy in effect: %s", redact_url(applied.get("HTTPS_PROXY", "")) or "none (direct)")
    return applied


def redact_url(url):
    """`http://user:pass@proxy:8080` -> `http://user:***@proxy:8080`, for anything user-visible.

    Lives here because the CLI's `status`, the Settings window and the debug log all need it, and a
    proxy URL with a password in it is exactly the line someone pastes into a chat asking for help.
    """
    if not url:
        return url
    scheme, _, rest = url.rpartition("://")
    userinfo, at, hostpart = rest.rpartition("@")
    if not at:
        return url
    user = userinfo.partition(":")[0]
    return f"{scheme}://{user}:***@{hostpart}" if scheme else f"{user}:***@{hostpart}"


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
SECRET_FIELDS = ("cadence_url", "cadence_session", "cadence_username", "cadence_password",
                 "cf_access_client_id", "cf_access_client_secret",
                 "spotify_client_id", "spotify_client_secret", "spotify_refresh_token",
                 "proxy_user", "proxy_password")
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


PROJECT_MARKER = "pyproject.toml"


def app_dir():
    """Where `.env` and `cadence_config.txt` live. Three cases, because there are three ways to run:

    **Frozen** — the folder holding the exe, so a portable copy carries its settings with it (USB
    stick, Downloads folder, wherever it was dropped).

    **A source checkout** — the project root, found by walking up from this file until `pyproject.toml`
    appears. Walked rather than counted, because `src/` layout put another directory between the
    package and the root and a hardcoded number of `dirname` calls is a silent breakage the next time
    anything moves.

    **pip-installed** — there is no project root above site-packages, so AppData. Writing a config
    file into site-packages would be wrong, and would vanish on the next upgrade.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isfile(os.path.join(here, PROJECT_MARKER)):
            return here
        parent = os.path.dirname(here)
        if parent == here:                      # reached the filesystem root: not a checkout
            path = appdata_dir()
            os.makedirs(path, exist_ok=True)
            return path
        here = parent


def find_icon(name="poulet.ico"):
    """The app icon: beside the exe first (so a portable copy can swap it), then inside the PyInstaller
    bundle, then the source folder for a dev run. None when there is none — every caller has a fallback.

    Lives here, with the other path logic, because ui/tray.py and ui/settings.py each had their own copy
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
    """The `.env` this process reads, or "" when there is none — and there is never one unless
    `use_env()` was called, which only the CLI does.

    Where the config already lives first (AppData for an installed copy under Program Files), then
    beside the app, so a portable copy carries its own. Two places, both of them "next to the app" in
    the sense the user means; nothing hunts through the home directory.
    """
    if not _env_enabled:
        return ""
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
    apply_proxy(cfg)
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
        # `cadence_username` / `cadence_password` ARE in DEFAULTS (the sign-in window stores them),
        # so they go through this loop like everything else: typed into the window they are kept,
        # supplied by the .env they are written back as "" and stay in the one file that owns them.
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
    launch. Lives here rather than in ui/tray.py so the CLI can ask the same question without importing
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
