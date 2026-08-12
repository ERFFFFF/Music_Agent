"""One Tk interpreter has to outlive the tests that build their own windows.

Tk unloads its library when the LAST interpreter is destroyed, and the next `Tk()` after that dies
with `invalid command name "tcl_findLibrary"`. Three tests here construct a real window and destroy
it when they are done, so whichever ran second hit exactly that — a failure with nothing to do with
what it was testing.

A single hidden root, created once and kept for the session, keeps the library loaded. It is never
shown and never used; it exists so that destroying a window is not also destroying Tk itself.
"""

import os
import sys

import pytest

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


@pytest.fixture(autouse=True)
def _proxy_env_is_not_shared():
    """No test may leave a proxy behind in the environment.

    `config.load_config()` applies the proxy as a side effect — that is the design, so that every
    entry point gets one without knowing it exists — which means ANY test that loads a config with a
    proxy in it exports `HTTPS_PROXY` for the rest of the process. It happened: a new config test set
    a proxy, and `test_httpmin`, several files later, tried to reach its own loopback server through
    `proxy.company.com:8080` and failed with something that looked nothing like the cause.
    """
    before = {name: os.environ.get(name) for name in PROXY_VARS}
    yield
    for name, value in before.items():
        os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)


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
