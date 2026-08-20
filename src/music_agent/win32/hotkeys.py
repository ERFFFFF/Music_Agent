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


def listen(bindings, on_error=None, poll_ms=250, stop=None, ready=None):
    """Register `{combo: callable}` and pump messages until KeyboardInterrupt (or `stop` is set).

    A combination that will not register is reported through `on_error(combo, message)` and skipped —
    one clash must not take the other four down with it. Returns the number that bound.

    `poll_ms` is why this waits rather than calling `GetMessageW`: that blocks inside the OS with no
    Python bytecode running, so Ctrl+C would not be noticed until the next hotkey press. Waiting in
    bounded slices costs nothing measurable and makes Ctrl+C land within one slice.

    `stop` is a `threading.Event` for the tray app, which has no Ctrl+C: saving Settings rebinds the
    hotkeys, and because registration is per-THREAD the only way to change them is to end this loop
    and start a new one. Checked once per slice, so it takes effect within `poll_ms`.

    `ready` is a `threading.Event` set once registration is done, so a caller on another thread can
    wait for the count instead of guessing.
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

    if ready is not None:
        ready.set()
    if not registered:
        return 0
    msg = wintypes.MSG()
    try:
        while stop is None or not stop.is_set():
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
