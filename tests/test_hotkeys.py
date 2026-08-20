"""Checks for `music_agent.win32.hotkeys` — moved here from the module's own selftest()."""

from music_agent.win32.hotkeys import *  # noqa: F401,F403 — the public surface under test
from music_agent.win32.hotkeys import (  # noqa: F401 — including the private names it exercises
    ERROR_HOTKEY_ALREADY_REGISTERED, MOD_NOREPEAT, ctypes, listen, parse)
import music_agent.win32.hotkeys as MODULE


def test_hotkeys():
    """Parsing is the part that fails silently — a combination that parses to the wrong key registers
    fine and then never fires. Everything else is checked live against the real API: a clash is
    refused, nothing leaks, and the loop actually stops on Ctrl+C."""
    import _thread
    import threading
    import time

    assert parse("ctrl+alt+up") == (0x0002 | 0x0001 | MOD_NOREPEAT, 0x26)
    assert parse("ctrl+alt+right") == (0x0002 | 0x0001 | MOD_NOREPEAT, 0x27)
    assert parse("ctrl+alt+l") == (0x0002 | 0x0001 | MOD_NOREPEAT, ord("L"))
    assert parse("CTRL+ALT+L") == parse("ctrl + alt + l"), "case and spaces must not matter"
    assert parse("ctrl+shift+alt+f9") == (0x0002 | 0x0004 | 0x0001 | MOD_NOREPEAT, 0x78)
    assert parse("win+7") == (0x0008 | MOD_NOREPEAT, ord("7"))
    for bad in ("ctrl+alt", "", "ctrl+nonsense", "ctrl+a+b"):
        try:
            parse(bad)
            raise AssertionError(f"{bad!r} should have been refused")
        except ValueError:
            pass

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    mods, key = parse("ctrl+alt+shift+f24")       # nothing sane is bound to this
    assert user32.RegisterHotKey(None, 9001, mods, key), ctypes.get_last_error()
    try:
        assert not user32.RegisterHotKey(None, 9002, mods, key), "a clash must be refused"
        assert ctypes.get_last_error() == ERROR_HOTKEY_ALREADY_REGISTERED
        # ...and `listen` must report that clash rather than pretend it bound
        problems = []
        assert listen({"ctrl+alt+shift+f24": lambda: None}, on_error=lambda c, m: problems.append(c)) == 0
        assert problems == ["ctrl+alt+shift+f24"], problems
    finally:
        user32.UnregisterHotKey(None, 9001)

    # A listen() that bound nothing must also have LEAKED nothing — the combination is free again.
    assert user32.RegisterHotKey(None, 9003, mods, key), "a refused listen left a registration behind"
    user32.UnregisterHotKey(None, 9003)

    # The whole reason the loop waits in slices instead of calling GetMessageW: Ctrl+C has to land.
    # GetMessageW blocks inside the OS with no Python bytecode running, so the interrupt would sit
    # unnoticed until the next hotkey press — on an agent nobody is pressing keys at, that is forever.
    # interrupt_main() is exactly what Ctrl+C does, so this is the real path.
    threading.Timer(0.4, _thread.interrupt_main).start()
    started = time.time()
    assert listen({"ctrl+alt+shift+f24": lambda: None}, poll_ms=50) == 1
    assert time.time() - started < 5, "Ctrl+C did not reach the loop"
    # ...and it unregistered on the way out, so the next run can bind the same combination
    assert user32.RegisterHotKey(None, 9004, mods, key), "listen leaked its registration on exit"
    user32.UnregisterHotKey(None, 9004)

    # `stop` is how the TRAY app rebinds. RegisterHotKey registers for the calling THREAD, so changing
    # a shortcut means ending this loop and starting a new one — and if the loop did not release its
    # registrations on the way out, the new thread would clash with the app's own old ones and report
    # every key as "already in use by another application".
    stop, ready = threading.Event(), threading.Event()
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            listen({"ctrl+alt+shift+f24": lambda: None}, poll_ms=50, stop=stop, ready=ready)),
        daemon=True)
    thread.start()
    assert ready.wait(5), "ready must be set once registration is done, not when the loop ends"
    assert user32.RegisterHotKey(None, 9005, mods, key) == 0, "it should be held while listening"
    stop.set()
    thread.join(5)
    assert not thread.is_alive(), "stop did not end the loop"
    assert result == [1], result
    assert user32.RegisterHotKey(None, 9006, mods, key), "stopping must release the registration"
    user32.UnregisterHotKey(None, 9006)

    # ready is set even when NOTHING bound, or a caller waiting on it would block for its full timeout
    # every time a combination clashed.
    ready2 = threading.Event()
    assert listen({"ctrl+nonsense": lambda: None}, ready=ready2, on_error=lambda c, m: None) == 0
    assert ready2.is_set()
