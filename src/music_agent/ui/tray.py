"""Music Agent — global hotkeys for whatever is playing your music.

Two modes (config.py `mode`, switchable in Settings):
  cadence — a Cadence account. The hotkey queues an intent that the open Cadence browser tab performs
            (Cadence's audio IS that tab). Needs nothing but a login, which is why this mode is the
            portable one: one exe, no Spotify developer app, and it works on a network where Spotify
            itself is blocked.
  spotify — the original: this machine's Spotify Connect device via the Web API. Needs a Spotify app's
            Client ID + Secret, asked for on first launch.

Everything the app remembers — mode, Cadence URL + session, Cloudflare token, Spotify keys, hotkeys —
lives in ONE file beside the app: cadence_config.txt (see config.py).

Both modes expose the same five actions (spotify.SpotifyController / cadence.CadenceController),
so everything below — hotkeys, tray, notifications — is mode-agnostic.
"""

import ctypes
import logging
import logging.handlers
import os
import sys
import threading
import time

import pystray
from PIL import Image

from music_agent import log
from music_agent.backends.cadence import CadenceError, client_from_config
from music_agent.config import (ACTIONS, appdata_dir, find_icon, is_configured, load_config,
                    save_config)
from music_agent.ui import login
from music_agent.ui.settings import open_settings
from music_agent.win32 import hotkeys

# this string is your “AppUserModelID”
MY_APP_ID = "com.erfffff.musicagent"


def initialize_logging():
    """Initialize logging to both file and console."""
    app_data_path = appdata_dir("Logs")
    os.makedirs(app_data_path, exist_ok=True)
    log_file = os.path.join(app_data_path, "music_agent_control.log")
    log_format = "%(asctime)s:%(levelname)s:%(message)s"
    # Rotated, because nothing ever deleted it: DEBUG level, every launch, appended forever. The copy
    # on this machine had reached 1.6 MB over two years — not a crisis, but a file that only grows is
    # a file that eventually matters, and it holds your server address and account name.
    # 1 MB x 2 keeps roughly the last few thousand launches' worth and can never exceed 3 MB.
    #
    # sys.stdout is None in the --noconsole build (there is no console to write to), and a
    # StreamHandler over it raises inside logging on EVERY record — swallowed by handleError, so the
    # only symptom is the work. Only add the console handler when there is a console.
    handlers = [logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=2,
                                                     encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.DEBUG, format=log_format, handlers=handlers)
    # Third-party libraries log at DEBUG too, and Pillow alone emits ~50 lines listing every image
    # plugin it can find. Since the Logs page exists to show what THIS app is doing, they are pinned
    # at WARNING -- their problems still surface, their bookkeeping does not.
    for noisy in ("PIL", "comtypes", "asyncio", "urllib3", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # The tray app's Logs page reads this buffer — the same one music_agent/cli.py -d prints — so a
    # problem described from one front end looks identical in the other. In memory only; see log.
    log.install(logging.DEBUG)
    logging.info("Logging initialized.")



# Single-instance enforcement
MUTEX_NAME = "Global\\MusicAgentMutex"
ERROR_ALREADY_EXISTS = 183


def create_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, MUTEX_NAME)  # handle intentionally leaked: the lock lives as
                                                    # long as the process, and closing it frees the name
    last_error = ctypes.get_last_error()
    if last_error == ERROR_ALREADY_EXISTS:
        logging.info("Another instance is already running.")
        sys.exit(0)
    elif last_error != 0:
        logging.error(f"Failed to create mutex: {last_error}")
        sys.exit(1)
    logging.info("Instance lock acquired.")




# Windows balloon notification
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


def notify(message, title="Music Agent"):
    """Balloon notification, off the hotkey thread — a 5s toast must not delay the next key press."""
    threading.Thread(target=create_notify_icon, args=(title, message), daemon=True).start()


# --------------------------------------------------------------------------- the controller (= the mode)
app_config = None
controller = None
tray_icon = None


def run_setup(cfg):
    """First run (or a copy that lost its credentials): ask which service, then set it up. Returns the
    chosen mode, or None if the user closed the window."""
    mode = login.choose_mode(cfg)
    if mode is None:
        return None
    cfg["mode"] = mode
    try:
        save_config(cfg)
    except OSError:
        pass  # a read-only config dir must not stop the app running for this session
    if mode == "spotify" and not login.spotify_setup(cfg):
        return None
    return mode


def build_controller(cfg, setup=True):
    """Build the object the hotkeys drive. None means "nothing to control": the user closed a setup
    window, or Spotify mode has no usable credentials.

    `setup` gates the launch-time windows so a Settings reload can rebuild quietly instead of throwing
    a chooser at someone who just changed a hotkey.
    """
    if setup and not is_configured(cfg) and run_setup(cfg) is None:
        return None

    if cfg["mode"] == "cadence":
        from music_agent.backends.cadence import CadenceController

        client = client_from_config(cfg)
        if not client.is_authenticated():
            # No session, or it expired/was revoked: this is the "login part" on launch. It appears
            # once per machine — Cadence re-signs the cookie on every call, so it stays valid.
            #
            # ...unless the account itself is stored, in which case sign in with it and say nothing.
            # This is the config file's own copy, put there by the sign-in window: the user typed it
            # into this app and asked it to keep it, so making them retype it because a cookie aged
            # out is the app forgetting on purpose. (Not the .env — that stays CLI-only, and with
            # use_env off these fields are simply empty unless the window filled them.)
            if cfg.get("cadence_username") and cfg.get("cadence_password"):
                try:
                    client.login(cfg["cadence_username"], cfg["cadence_password"])
                    return CadenceController(client)
                except CadenceError as e:
                    # Fall through to the window rather than dying: a changed password, a sleeping
                    # server and a revoked account all land here, and the window can fix all three —
                    # pre-filled with what was tried, which is exactly what needs correcting.
                    logging.warning("Stored sign-in failed (%s) — asking instead.", e)
            # Gated on `setup` as well, because is_authenticated() is a live request that also returns
            # False for a sleeping or unreachable server. Without the gate, saving a hotkey while the
            # Cadence stack was scaled to zero threw a full username/password window at someone who
            # was already signed in.
            if not setup:
                return None
            client = login.sign_in(cfg)
            if client is None:
                return None
        return CadenceController(client)

    from music_agent.backends.spotify import SpotifyConfigError, controller_from_config

    try:
        return controller_from_config(cfg)
    except SpotifyConfigError as e:
        # No longer fatal: a fresh copy legitimately has no Spotify keys, and the fix (enter them, or
        # switch to Cadence) is one Settings click away — exiting would hide the message with the app.
        logging.error(str(e))
        notify(str(e))
        return None
    except Exception as e:
        logging.error(f"Spotify init error: {e}", exc_info=True)
        notify("Spotify sign-in failed — see the log.")
        return None


def perform(action_id):
    """Run one action, on a thread of its own (see _fire). Every outcome is logged: this is the app
    answering "did my shortcut work?", and until now the two most common answers — "no controller" and
    "no Cadence tab was listening" — produced a balloon and not one line anywhere."""
    name = ACTIONS[action_id]
    if controller is None:
        logging.warning("%s ignored: nothing to control — not signed in", action_id)
        notify("Not signed in — open Settings to choose an account.")
        return
    started = time.monotonic()
    try:
        message = getattr(controller, name)()
    except Exception as e:  # an action must never die on an unexpected error
        logging.error("%s failed: %s", action_id, e, exc_info=True)
        notify("Something went wrong — see the log.")
        return
    logging.info("%s finished in %.2fs", action_id, time.monotonic() - started)
    if message:
        notify(message, title="Now Playing" if name == "show_current" else "Music Agent")


def _fire(action_id):
    """What the hotkey itself does: say so, then hand off.

    The handoff is the point. A Cadence call is allowed 25 seconds (it has to cover a Sablier wake),
    and whatever thread dispatches hotkeys can do nothing else while that runs — with the old
    `keyboard` hook that meant every key pressed during the wait was silently DISCARDED, and here it
    would stall the message loop. One short-lived thread per press costs nothing and keeps the loop
    free.
    """
    logging.info("hotkey pressed: %s", action_id)
    threading.Thread(target=perform, args=(action_id,), daemon=True, name=f"action-{action_id}").start()


# The hotkey thread, and the Event that ends it. Rebinding means ending the loop and starting a new
# one, because RegisterHotKey registers for the THREAD that calls it.
_hotkey_thread = None
_hotkey_stop = None


def setup_hotkeys():
    """Register the configured global hotkeys, through Windows itself.

    `win32/hotkeys.py` (RegisterHotKey + a message loop) is what the CLI has used since 4.2; the tray
    app kept `keyboard`, whose WH_KEYBOARD_LL hook loses hotkeys in three ways that are all permanent
    and all completely silent — measured against keyboard 0.13.5's own dispatch code:

      * It looks a hotkey up by `tuple(sorted(_pressed_events))`, an exact match on the set of keys it
        believes are held. A key-up missed while the UAC/Ctrl+Alt+Del secure desktop or the Win+L lock
        screen is up — or under an elevated window, which UIPI does not deliver to a non-elevated
        hook — leaves a phantom in that set FOREVER. Nothing matches again, and nothing is logged. It
        never self-heals.
      * Callbacks are invoked from `pre_process_event`, which has no try/except (unlike
        `invoke_handlers` twenty lines away). One raise ends the single queue-draining thread and
        every hotkey is dead until restart.
      * That thread reads the pressed-key set at DEQUEUE time, so a key pressed while a slow callback
        runs is matched after its keys are already released, and dropped — not delayed.

    RegisterHotKey has none of those: no hook, no key table, no dispatch thread of its own, and no
    administrator rights. The trade is that a combination already owned by another application fails
    with 1409 instead of being stolen — which is now SAID, in the log and in a balloon, rather than
    leaving a key that silently does nothing.

    `keyboard` stays a dependency for one job only: Settings RECORDS a combination as you press it,
    and RegisterHotKey cannot do that.
    """
    global _hotkey_thread, _hotkey_stop
    stop_hotkeys()

    bindings, clashes = {}, []
    for action_id in ACTIONS:
        combo = (app_config["hotkeys"].get(action_id) or "").strip()
        if not combo:
            logging.warning("hotkey %s is not bound to anything", action_id)
            continue
        # One dict keyed by COMBINATION, so two actions on the same keys can't silently shadow each
        # other — the second registration would fail with 1409 and be reported as a clash.
        bindings[combo] = lambda a=action_id: _fire(a)

    def on_error(combo, message):
        clashes.append(combo)
        logging.error("hotkey %r did not register: %s", combo, message)

    ready = threading.Event()
    _hotkey_stop = threading.Event()
    _hotkey_thread = threading.Thread(
        target=lambda: hotkeys.listen(bindings, on_error=on_error, stop=_hotkey_stop, ready=ready),
        daemon=True, name="hotkeys")
    _hotkey_thread.start()
    ready.wait(5)
    bound = len(bindings) - len(clashes)
    logging.info("hotkeys registered with Windows: %d/%d %s", bound, len(bindings), sorted(bindings))
    if clashes:
        notify("Another app already uses " + ", ".join(sorted(clashes)) +
               " — pick different keys in Settings.")


def stop_hotkeys():
    """End the hotkey thread and release its registrations. Returns once it has actually gone."""
    global _hotkey_thread, _hotkey_stop
    if _hotkey_stop is not None:
        _hotkey_stop.set()
    if _hotkey_thread is not None:
        # Slightly longer than win32.hotkeys' 250ms slice: the loop checks `stop` once per slice, and
        # leaving before UnregisterHotKey has run would make the next bind clash with ourselves.
        _hotkey_thread.join(2)
    _hotkey_thread = _hotkey_stop = None


def reload_config():
    """Settings saved: re-register hotkeys, and rebuild the controller if the account/mode changed."""
    global app_config, controller
    previous = app_config
    app_config = load_config()
    setup_hotkeys()
    # The credential fields are in here too: signing in (or out) from Settings has to reach the LIVE
    # controller, which otherwise keeps using the old client — a revoked session kept reporting
    # "sign in again" after a successful re-login, and Sign out kept working until the app restarted.
    changed = any(previous.get(k) != app_config.get(k)
                  for k in ("mode", "cadence_url", "cf_access_client_id", "cf_access_client_secret",
                            "cadence_session", "spotify_client_id"))
    if changed or controller is None:
        # setup=False: Settings has its own sign-in / credentials buttons, so don't ALSO pop the
        # first-run chooser at someone who just saved a hotkey.
        controller = build_controller(app_config, setup=False)
    if tray_icon is not None:
        tray_icon.title = f"Music Agent ({app_config['mode']})"   # or it reports the mode it started in
    logging.info("Config reloaded (mode=%s).", app_config["mode"])


def create_tray_icon():
    """Create and return a pystray Icon with right-click menu."""
    icon_path = find_icon()
    try:
        icon_image = Image.open(icon_path)
    except Exception as e:  # noqa: BLE001 — includes icon_path being None
        logging.warning(f"Could not load icon from {icon_path}: {e}. Using fallback.")
        icon_image = Image.new("RGBA", (64, 64), (70, 130, 180, 255))

    menu = pystray.Menu(
        pystray.MenuItem(
            "Settings",
            lambda: open_settings(on_save_callback=reload_config),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, _item: icon.stop()),
    )

    icon = pystray.Icon(
        name="MusicAgent",
        icon=icon_image,
        title=f"Music Agent ({app_config['mode']})",
        menu=menu,
    )
    return icon


def start():
    """The side effects that used to run at IMPORT time: the app id, the log, the single-instance
    mutex, and reading the config.

    Importing a module must not reconfigure logging and must certainly not `sys.exit()` — which is
    what `create_single_instance()` did at module level, so `import music_agent.ui.tray` killed the
    interpreter whenever another copy was running. Everything that changes global state now happens
    when someone actually starts the app.
    """
    global app_config

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(MY_APP_ID)
    initialize_logging()
    create_single_instance()
    app_config = load_config()


def main():
    global controller, tray_icon

    start()
    # build_controller mutates app_config in place when setup changes the mode, so the tray title
    # below already reads the chosen one — no re-read needed.
    controller = build_controller(app_config)
    if controller is None and not is_configured(app_config):
        # Setup was closed without choosing anything — there is genuinely nothing to control. A
        # CONFIGURED copy whose controller failed to build (dead token, no network) still goes to the
        # tray: the fix lives in Settings, and quitting would hide it.
        logging.info("Setup cancelled — nothing to control, exiting.")
        sys.exit(0)

    setup_hotkeys()   # starts its own thread, which is where the message loop has to live

    # Run tray icon on main thread (blocks until Quit)
    tray_icon = icon = create_tray_icon()
    logging.info("System tray icon started.")
    icon.run()

    # Cleanup after icon.stop()
    stop_hotkeys()
    logging.info("Music Agent shut down.")


if __name__ == "__main__":
    main()
