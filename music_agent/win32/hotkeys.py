"""Global hotkeys straight from Win32, so the CLI needs no packages at all.

`RegisterHotKey` + a message loop IS the operating system's global-hotkey mechanism. The `keyboard`
package instead installs a `WH_KEYBOARD_LL` hook, which sees every keystroke on the machine, needs
administrator rights on some systems, and was the CLI's last remaining dependency. The OS API is
smaller, needs no elevation, and cannot miss a combination because another hook ate it first.

The trade, stated plainly:

- **One combination, one owner.** `RegisterHotKey` fails with ERROR_HOTKEY_ALREADY_REGISTERED (1409)
  when something else already holds it, where a low-level hook would have quietly stolen it. That is
  the honest behaviour — `listen` reports which combination is taken instead of binding a dead key —
  but it does mean a clash needs rebinding rather than winning by force.
- **Registration is per THREAD.** The messages arrive on the thread that called `RegisterHotKey`, so
  the loop below has to run there too. For the CLI that is the main thread, which is exactly where
  `run` blocks anyway.
- **Windows only.** `ctypes.wintypes` raises on import elsewhere, which is why this module is imported
  lazily, from the one command that needs it.

ponytail: no key-capture, no suppression, no remapping — the five actions bind a combination and that
is the whole feature. `keyboard` is still the answer if the GUI ever needs to RECORD a combination
(settings.py does, and still uses it).
"""

import ctypes
from ctypes import wintypes

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF
MOD_NOREPEAT = 0x4000          # holding the keys fires once, not sixty times a second
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

MODIFIERS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004,
             "win": 0x0008, "windows": 0x0008, "cmd": 0x0008}

# Only the named keys need a table: a single character maps to its own virtual-key code, because for
# A-Z and 0-9 the VK code IS the uppercase ASCII value.
KEYS = {
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "printscreen": 0x2C, "pause": 0x13,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},          # VK_F1 is 0x70
}


def parse(combo):
    """"ctrl+alt+up" -> (modifier flags, virtual-key code). Raises ValueError on anything else.

    Accepts what `keyboard` accepts for these five defaults, so an existing cadence_config.txt keeps
    working across the switch without anyone re-entering their shortcuts.
    """
    mods, key, seen = 0, None, []
    for part in (combo or "").lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif part in KEYS:
            key, seen = KEYS[part], seen + [part]
        elif len(part) == 1:
            key, seen = ord(part.upper()), seen + [part]
        else:
            raise ValueError(f"unknown key {part!r} in {combo!r}")
    if key is None:
        raise ValueError(f"{combo!r} names no key, only modifiers")
    if len(seen) > 1:
        # Windows binds ONE key plus modifiers. Refusing beats registering whichever came last.
        raise ValueError(f"{combo!r} names more than one key ({', '.join(seen)})")
    return mods | MOD_NOREPEAT, key


def listen(bindings, on_error=None, poll_ms=250):
    """Register `{combo: callable}` and pump messages until KeyboardInterrupt.

    A combination that will not register is reported through `on_error(combo, message)` and skipped —
    one clash must not take the other four down with it. Returns the number that bound.

    `poll_ms` is why this waits rather than calling `GetMessageW`: that blocks inside the OS with no
    Python bytecode running, so Ctrl+C would not be noticed until the next hotkey press. Waiting in
    bounded slices costs nothing measurable and makes Ctrl+C land within one slice.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL,
                                                 wintypes.DWORD, wintypes.DWORD]

    registered = {}
    for hotkey_id, (combo, callback) in enumerate(bindings.items(), start=1):
        try:
            mods, key = parse(combo)
        except ValueError as e:
            if on_error:
                on_error(combo, str(e))
            continue
        if not user32.RegisterHotKey(None, hotkey_id, mods, key):
            code = ctypes.get_last_error()
            if on_error:
                on_error(combo, "already in use by another application"
                         if code == ERROR_HOTKEY_ALREADY_REGISTERED else f"Windows error {code}")
            continue
        registered[hotkey_id] = callback

    if not registered:
        return 0
    msg = wintypes.MSG()
    try:
        while True:
            user32.MsgWaitForMultipleObjects(0, None, False, poll_ms, QS_ALLINPUT)
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY and msg.wParam in registered:
                    registered[msg.wParam]()
    except KeyboardInterrupt:
        pass
    finally:
        for hotkey_id in registered:
            user32.UnregisterHotKey(None, hotkey_id)
    return len(registered)


def selftest():
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
    print("winhotkeys self-check ok")


if __name__ == "__main__":
    selftest()
