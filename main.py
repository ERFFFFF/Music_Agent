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

Both modes expose the same five actions (spotify_backend.SpotifyController / cadence.CadenceController),
so everything below — hotkeys, tray, notifications — is mode-agnostic.
"""

import ctypes
import logging
import os
import sys
import threading
import time

import keyboard
import pystray
from PIL import Image

from cadence import client_from_config
from config import ACTIONS, appdata_dir, find_icon, is_configured, load_config, save_config
import login_ui
from settings_ui import open_settings

# this string is your “AppUserModelID”
MY_APP_ID = "com.erfffff.musicagent"
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(MY_APP_ID)


def initialize_logging():
    """Initialize logging to both file and console."""
    app_data_path = appdata_dir("Logs")
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


create_single_instance()


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
app_config = load_config()
controller = None
tray_icon = None


def run_setup(cfg):
    """First run (or a copy that lost its credentials): ask which service, then set it up. Returns the
    chosen mode, or None if the user closed the window."""
    mode = login_ui.choose_mode(cfg)
    if mode is None:
        return None
    cfg["mode"] = mode
    try:
        save_config(cfg)
    except OSError:
        pass  # a read-only config dir must not stop the app running for this session
    if mode == "spotify" and not login_ui.spotify_setup(cfg):
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
        from cadence import CadenceController

        client = client_from_config(cfg)
        if not client.is_authenticated():
            # No session, or it expired/was revoked: this is the "login part" on launch. It appears
            # once per machine — Cadence re-signs the cookie on every call, so it stays valid.
            #
            # Gated on `setup` as well, because is_authenticated() is a live request that also returns
            # False for a sleeping or unreachable server. Without the gate, saving a hotkey while the
            # Cadence stack was scaled to zero threw a full username/password window at someone who
            # was already signed in.
            if not setup:
                return None
            client = login_ui.sign_in(cfg)
            if client is None:
                return None
        return CadenceController(client)

    from spotify_backend import SpotifyConfigError, controller_from_config

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


def action(name):
    """Bind one controller method to a hotkey. Wrapped because this runs on the keyboard thread, where
    an uncaught exception kills that hotkey silently and permanently."""
    def run():
        if controller is None:
            notify("Not signed in — open Settings to choose an account.")
            return
        try:
            message = getattr(controller, name)()
        except Exception as e:  # noqa: BLE001 — a hotkey must never die on an unexpected error
            logging.error(f"{name} failed: {e}", exc_info=True)
            notify("Something went wrong — see the log.")
            return
        if message:
            notify(message, title="Now Playing" if name == "show_current" else "Music Agent")
    return run


HOTKEY_ACTIONS = {action_id: action(method) for action_id, method in ACTIONS.items()}


def setup_hotkeys():
    """Register global hotkeys from current config."""
    for action_id, func in HOTKEY_ACTIONS.items():
        hotkey_str = app_config["hotkeys"].get(action_id)
        if hotkey_str:
            try:
                keyboard.add_hotkey(hotkey_str, func)
            except Exception as e:
                logging.error(f"Failed to register hotkey '{hotkey_str}' for {action_id}: {e}")
    logging.info("Hotkeys registered: %s", app_config["hotkeys"])


def reload_config():
    """Settings saved: re-register hotkeys, and rebuild the controller if the account/mode changed."""
    global app_config, controller
    keyboard.unhook_all_hotkeys()
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


def main():
    global controller, tray_icon
    # build_controller mutates app_config in place when setup changes the mode, so the tray title
    # below already reads the chosen one — no re-read needed.
    controller = build_controller(app_config)
    if controller is None and not is_configured(app_config):
        # Setup was closed without choosing anything — there is genuinely nothing to control. A
        # CONFIGURED copy whose controller failed to build (dead token, no network) still goes to the
        # tray: the fix lives in Settings, and quitting would hide it.
        logging.info("Setup cancelled — nothing to control, exiting.")
        sys.exit(0)

    setup_hotkeys()

    # Run keyboard listener on a daemon thread
    kb_thread = threading.Thread(target=keyboard.wait, daemon=True)
    kb_thread.start()

    # Run tray icon on main thread (blocks until Quit)
    tray_icon = icon = create_tray_icon()
    logging.info("System tray icon started.")
    icon.run()

    # Cleanup after icon.stop()
    keyboard.unhook_all_hotkeys()
    logging.info("Music Agent shut down.")


if __name__ == "__main__":
    main()
