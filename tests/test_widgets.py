"""Checks for `music_agent.ui.widgets` — the masked entry and its eye.

Worth a test for one reason: the failure mode is a password rendered in the clear, and nothing about
the code would look wrong. `show` is the whole mechanism, so `show` is what gets asserted.
"""

import gc
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("the GUI is Windows-only here", allow_module_level=True)

ctk = pytest.importorskip("customtkinter")          # the `gui` extra
widgets = pytest.importorskip("music_agent.ui.widgets")


def test_secret_entry_starts_masked_and_toggles():
    root = ctk.CTk()
    root.withdraw()                                  # a real Tk root, off screen: no window flashes up
    try:
        var = ctk.StringVar(master=root, value="hunter2")
        entry = widgets.secret_entry(root, var)

        # Masked to start with. A saved password must not become readable just because a window opened.
        assert entry.cget("show") == widgets.MASK, entry.cget("show")

        eye = [child for child in root.winfo_children() if isinstance(child, ctk.CTkButton)]
        assert len(eye) == 1, "no eye button was packed next to the entry"
        assert eye[0].cget("text") == widgets.EYE_SHOW

        eye[0].invoke()
        assert entry.cget("show") == "", "the eye did not reveal the value"
        assert eye[0].cget("text") == widgets.EYE_HIDE, "the button must say what it does next"

        eye[0].invoke()
        assert entry.cget("show") == widgets.MASK, "the eye did not put the dots back"
        assert eye[0].cget("text") == widgets.EYE_SHOW

        # Revealing must not disturb the value itself — it is the caller's variable, not a copy.
        assert var.get() == "hunter2"
    finally:
        # Tear the widgets down BEFORE the root, and collect in between. A CTkFont or a StringVar
        # finalised after its interpreter is gone raises "main thread is not in main loop" from a
        # __del__, where pytest reports it as an unraisable-exception warning attached to whichever
        # test happens to be running at the time — a confusing failure to inherit for one line of
        # cleanup.
        del entry, eye
        for child in root.winfo_children():
            child.destroy()
        gc.collect()
        root.destroy()
        gc.collect()


def test_the_glyphs_are_inside_tcls_range():
    """Tcl 8.6 — what Python ships — cannot hold a character above U+FFFF: the obvious eye emoji
    (U+1F441) raises the moment a widget is built with it, on the user's machine and not here."""
    for glyph in (widgets.MASK, widgets.EYE_SHOW, widgets.EYE_HIDE):
        assert ord(glyph) <= 0xFFFF, f"{glyph!r} is above the BMP and Tk will refuse it"
