"""Checks for the sign-in window: it must SHOW what is stored, and mask only the password.

These exist because both halves were silently reverted once — the pre-fill in one edit, the eye in
another — and every test still passed. Nothing else looks at what the window actually renders, so
"the field is empty" and "the password is in the clear" were both invisible failures.

The window is built and inspected, never shown to a human: `mainloop` is replaced with a script that
asserts and destroys.
"""

import gc
import sys
import tempfile

import pytest

if sys.platform != "win32":
    pytest.skip("the GUI is Windows-only here", allow_module_level=True)

ctk = pytest.importorskip("customtkinter")               # the `gui` extra
login_ui = pytest.importorskip("music_agent.ui.login")

from music_agent import config                           # noqa: E402 — after the skip guard
from music_agent.ui import widgets                       # noqa: E402


def _widgets_of(parent, kind, out=None):
    out = [] if out is None else out
    for child in parent.winfo_children():
        if isinstance(child, kind):
            out.append(child)
        _widgets_of(child, kind, out)
    return out


def test_the_sign_in_window_opens_on_what_is_stored():
    real_mainloop = ctk.CTk.mainloop
    real_app_dir = config.app_dir
    seen = {}
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            config.use_env(False)
            cfg = config.load_config()
            cfg.update({"cadence_url": "https://cadence.example.com", "cadence_username": "erfffff",
                        "cadence_password": "correct-horse-battery"})

            def script(root):
                entries = _widgets_of(root, ctk.CTkEntry)
                seen["values"] = [e.get() for e in entries]
                seen["masked"] = [e.cget("show") for e in entries]
                seen["eyes"] = [b for b in _widgets_of(root, ctk.CTkButton)
                                if b.cget("text") in (widgets.EYE_SHOW, widgets.EYE_HIDE)]
                seen["proxy_button"] = [b for b in _widgets_of(root, ctk.CTkButton)
                                        if b.cget("text") == "Proxy"]
                root.destroy()

            ctk.CTk.mainloop = script
            login_ui.LoginWindow(None, dict(cfg))

        # Server and username are shown as themselves; the password is dots with an eye beside it.
        assert seen["values"] == ["https://cadence.example.com", "erfffff", "correct-horse-battery"], \
            seen["values"]
        assert seen["masked"] == ["", "", widgets.MASK], \
            f"exactly one field must be masked, and it must be the password: {seen['masked']}"
        assert len(seen["eyes"]) == 1, "the password field has no eye to reveal it"
        # The proxy has to be reachable from HERE: on a network that mandates one this window is the
        # first thing that fails, and Settings is behind a tray icon that does not exist yet.
        assert len(seen["proxy_button"]) == 1, "no way to configure a proxy before signing in"
    finally:
        ctk.CTk.mainloop = real_mainloop
        config.app_dir = real_app_dir
        gc.collect()
