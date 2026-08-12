"""One Tk interpreter has to outlive the tests that build their own windows.

Tk unloads its library when the LAST interpreter is destroyed, and the next `Tk()` after that dies
with `invalid command name "tcl_findLibrary"`. Three tests here construct a real window and destroy
it when they are done, so whichever ran second hit exactly that — a failure with nothing to do with
what it was testing.

A single hidden root, created once and kept for the session, keeps the library loaded. It is never
shown and never used; it exists so that destroying a window is not also destroying Tk itself.
"""

import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def _tk_stays_loaded():
    root = None
    if sys.platform == "win32":
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
        except Exception:          # noqa: BLE001 — no display, no Tk, no problem: nothing to keep alive
            root = None
    yield
    if root is not None:
        try:
            root.destroy()
        except Exception:          # noqa: BLE001 — teardown of a thing the tests never touched
            pass
