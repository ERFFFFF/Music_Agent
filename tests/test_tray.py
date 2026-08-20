"""`tray.reload_config` — the Save button's callback, and the one path nothing else reached.

It runs on the Settings window's own thread, which swallows the traceback, and its FIRST act is to
tear down every hotkey. So a crash in the middle leaves all five keys dead with nothing on screen
saying why, until the app is restarted — which is exactly what shipped when the `src/` move rebound
`app_config` to None here instead of re-reading the config.

Everything it touches is faked: no hotkey registration, no window, no network. The point is the wiring.
"""

import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

if sys.platform != "win32":
    pytest.skip("the tray app is Windows-only", allow_module_level=True)

tray = pytest.importorskip("music_agent.ui.tray")   # customtkinter/pystray/PIL — the `gui` extra


def test_reload_config():
    fresh = {"mode": "spotify", "hotkeys": {"play_pause": "ctrl+alt+up"},
             "spotify_client_id": "new-app"}
    stale = {"mode": "cadence", "hotkeys": {"play_pause": "ctrl+alt+up"}, "spotify_client_id": ""}
    bound, stopped, built = [], [], []

    def fake_listen(bindings, on_error=None, poll_ms=250, stop=None, ready=None):
        bound.append(sorted(bindings))
        if ready is not None:
            ready.set()
        while stop is not None and not stop.is_set():
            time.sleep(0.01)
        stopped.append(True)
        return len(bindings)

    fake_hotkeys = mock.Mock()
    fake_hotkeys.listen.side_effect = fake_listen

    with mock.patch.object(tray, "hotkeys", fake_hotkeys), \
            mock.patch.object(tray, "load_config", lambda: fresh), \
            mock.patch.object(tray, "build_controller", lambda cfg, setup=True: built.append(setup) or "ctl"), \
            mock.patch.object(tray, "app_config", stale), \
            mock.patch.object(tray, "controller", "old"), \
            mock.patch.object(tray, "tray_icon", mock.Mock()):
        try:
            tray.reload_config()

            assert tray.app_config is fresh, "the saved config has to be re-read, not dropped"
            assert bound == [["ctrl+alt+up"]], bound
            # The mode changed, so the live controller must be rebuilt — a Settings save that only
            # rewrites the file leaves the running app driving the account it started with.
            assert tray.controller == "ctl" and built == [False], built
            assert tray.tray_icon.title == "Music Agent (spotify)", tray.tray_icon.title

            # Rebinding has to END the old listener first: RegisterHotKey is per THREAD, so a second
            # thread registering the same combination would clash with the app's own first one and
            # every key would come back "already in use by another application".
            tray.reload_config()
            assert stopped, "the previous hotkey thread must be stopped before rebinding"
            assert len(bound) == 2, bound
        finally:
            tray.stop_hotkeys()


def test_a_slow_action_does_not_block_the_hotkey_loop():
    """A Cadence call gets 25 seconds (it has to cover a Sablier wake). Whatever thread dispatches
    hotkeys must not be the one waiting for it — under the old `keyboard` hook that meant every key
    pressed during the wait was silently DISCARDED, and here it would stall the message loop, so the
    next press would do nothing at all."""
    running = threading.Event()
    release = threading.Event()

    class SlowController:
        def play_pause(self):
            running.set()
            release.wait(5)

    with mock.patch.object(tray, "controller", SlowController()), \
            mock.patch.object(tray, "notify", lambda *a, **k: None):
        started = time.monotonic()
        tray._fire("play_pause")
        elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"_fire blocked the caller for {elapsed:.2f}s"
    assert running.wait(5), "the action never actually ran"
    release.set()


def test_a_stored_account_signs_in_without_a_window():
    """An expired session must not put a password box in front of someone whose password the app is
    already holding — that is the whole reason it was asked to keep it.

    The window is the fallback, not the first move: `login.sign_in` here would mean a real Tk window
    in a test run, so if this ever regresses the suite hangs on a dialog rather than failing quietly.
    """
    signed_in_with = []

    class FakeClient:
        def is_authenticated(self):
            return False                       # the session aged out, as it does after ~7 days

        def login(self, user, password):
            signed_in_with.append((user, password))
            return {"username": user}

    cfg = {"mode": "cadence", "cadence_url": "https://cadence.example",
           "cadence_username": "erfffff", "cadence_password": "stored-pw", "hotkeys": {}}

    with mock.patch.object(tray, "client_from_config", lambda _cfg: FakeClient()), \
            mock.patch.object(tray, "is_configured", lambda _cfg: True):
        controller = tray.build_controller(cfg)

    assert signed_in_with == [("erfffff", "stored-pw")], signed_in_with
    assert controller is not None and controller.client.__class__ is FakeClient


def test_a_rejected_stored_account_falls_back_to_the_window():
    """A password that stopped working (rotated server-side) has to reach the window, pre-filled, not
    kill the launch: the window is the only thing that can fix it."""
    from music_agent.backends.cadence import CadenceError

    class RefusingClient:
        def is_authenticated(self):
            return False

        def login(self, _user, _password):
            raise CadenceError("Wrong username or password.")

    cfg = {"mode": "cadence", "cadence_url": "https://cadence.example",
           "cadence_username": "erfffff", "cadence_password": "old-pw", "hotkeys": {}}
    asked = []

    with mock.patch.object(tray, "client_from_config", lambda _cfg: RefusingClient()), \
            mock.patch.object(tray, "is_configured", lambda _cfg: True), \
            mock.patch.object(tray.login, "sign_in", lambda _cfg: asked.append(_cfg) or None):
        assert tray.build_controller(cfg) is None      # user closed the window
    assert len(asked) == 1, "a refused stored password must fall back to the sign-in window"


def test_the_tray_app_never_reads_a_dotenv():
    """The `.env` is the CLI's mechanism. The tray app — portable or installed — is configured from
    cadence_config.txt and its own windows, so a file it never shows you must not be able to overrule
    what you typed into Settings.

    Run in a SUBPROCESS on purpose: `config._env_enabled` is process-global, and test_cli.py turns it
    on. In this process the check would pass or fail on test ORDER, which is no check at all. A fresh
    interpreter that imports the tray app is exactly what launching the exe does.
    """
    code = ("import music_agent.ui.tray, music_agent.config as c; "
            "print(repr((c._env_enabled, c.env_path(), c.env_config())))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "(False, '', {})", done.stdout


def test_reload_config_leaves_an_unchanged_controller_alone():
    """Saving a hotkey must not tear down a working session: rebuilding a Cadence controller is a
    live request, and doing it on every save made a sleeping server look like a signed-out one."""
    same = {"mode": "cadence", "hotkeys": {"next_track": "ctrl+alt+right"}, "cadence_session": "s"}

    with mock.patch.object(tray, "hotkeys", mock.Mock()), \
            mock.patch.object(tray, "load_config", lambda: dict(same)), \
            mock.patch.object(tray, "build_controller", mock.Mock(side_effect=AssertionError("rebuilt"))), \
            mock.patch.object(tray, "app_config", dict(same)), \
            mock.patch.object(tray, "controller", "live"), \
            mock.patch.object(tray, "tray_icon", None):
        try:
            tray.reload_config()
            assert tray.controller == "live"
        finally:
            tray.stop_hotkeys()
