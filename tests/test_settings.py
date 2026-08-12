"""Checks for `music_agent.ui.settings` that need the real window.

One thing: Sign out has to clear the stored ACCOUNT, not just the session. It never had to before —
the app had no password to forget — and now that an expired session re-signs itself in from what is
stored, leaving the account behind would make Sign out reverse itself on the next launch.

Driven against the real window and the real client, because the two clear different things by
different routes (`forget()` writes through `update_config`; the account is cleared here) and a fake
client is exactly the kind of stand-in that gets that interaction wrong. Verified by sabotage: remove
the clearing and this fails on `cadence_username`.

`mainloop` is replaced — build, act, destroy — so nothing waits for a human.
"""

import gc
import json
import os
import sys
import tempfile

import pytest

if sys.platform != "win32":
    pytest.skip("the GUI is Windows-only here", allow_module_level=True)

ctk = pytest.importorskip("customtkinter")               # the `gui` extra
settings_ui = pytest.importorskip("music_agent.ui.settings")

from music_agent import config                           # noqa: E402 — after the skip guard


def test_sign_out_clears_the_session_and_the_stored_account():
    real_app_dir, real_mainloop = config.app_dir, ctk.CTk.mainloop
    real_build = settings_ui.SettingsWindow._build_ui
    window = {}
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            config.use_env(False)
            cfg = config.load_config()
            cfg.update({"mode": "cadence", "cadence_url": "https://cadence.example",
                        "cadence_session": "a-signed-cookie", "cadence_username": "erfffff",
                        "cadence_password": "correct-horse-battery"})
            config.save_config(cfg)
            assert config.load_config()["cadence_password"] == "correct-horse-battery"

            def build(self):                              # mainloop runs on the ROOT, not the window
                window["it"] = self
                real_build(self)

            def script(root):
                assert window["it"].account_btn.cget("text") == "Sign out"
                window["it"]._cadence_sign_in()           # exactly what the button's command is
                root.destroy()

            settings_ui.SettingsWindow._build_ui = build
            ctk.CTk.mainloop = script
            settings_ui._window_open = False              # another test may have left the guard set
            settings_ui.SettingsWindow()

            after = config.load_config()
            assert after["cadence_session"] == "", "the cookie came back"
            assert after["cadence_username"] == "" and after["cadence_password"] == "", after
            # ...and nothing readable is left behind either.
            raw = open(config.config_path(), encoding="utf-8").read()
            assert "correct-horse-battery" not in raw and "erfffff" not in raw
            assert json.loads(raw)["mode"] == "cadence", "sign out is not a factory reset"
    finally:
        settings_ui.SettingsWindow._build_ui = real_build
        ctk.CTk.mainloop = real_mainloop
        settings_ui._window_open = False
        config.app_dir = real_app_dir
        os.environ.pop("HTTPS_PROXY", None)
        gc.collect()


def test_settings_shows_the_saved_proxy_and_picks_up_one_saved_elsewhere():
    """Two halves of the same trap.

    The boxes must show what is stored — that is the whole point of storing it. And they must be
    RE-read after a child window closes: `_save` writes the StringVars, not `self.config`, so a proxy
    typed into the sign-in window's Proxy dialog was still showing as empty here and the next Save
    wrote that emptiness straight over it. Reloading only the dict looked like a fix and was not.
    """
    real_app_dir, real_mainloop = config.app_dir, ctk.CTk.mainloop
    real_build = settings_ui.SettingsWindow._build_ui
    window = {}
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            config.use_env(False)
            cfg = config.load_config()
            cfg.update({"proxy_url": "proxy.company.com:8080", "proxy_user": "CORP\\me",
                        "proxy_password": "pw with spaces "})
            config.save_config(cfg)

            def build(self):
                window["it"] = self
                real_build(self)

            def script(root):
                win = window["it"]
                assert win.proxy_vars["proxy_url"].get() == "proxy.company.com:8080"
                assert win.proxy_vars["proxy_user"].get() == "CORP\\me"
                assert win.proxy_vars["proxy_password"].get() == "pw with spaces "

                # Something else writes a different proxy while this window is open — which is what
                # the sign-in window's Proxy dialog does, through _with_hidden_window.
                latest = config.load_config()
                latest["proxy_url"] = "other.company.com:3128"
                config.save_config(latest)
                win._with_hidden_window(lambda: None)
                assert win.proxy_vars["proxy_url"].get() == "other.company.com:3128", \
                    "the boxes still show the old proxy, and Save would write it back"

                # Press Save for real — the assertions below are about what _save() writes, and
                # reading back what the test itself stored would prove nothing about it.
                win.proxy_vars["proxy_password"].set("  pw with spaces  ")
                win.proxy_vars["proxy_url"].set("  other.company.com:3128  ")
                win._save()                       # destroys the window, ending this scripted loop

            settings_ui.SettingsWindow._build_ui = build
            ctk.CTk.mainloop = script
            settings_ui._window_open = False
            settings_ui.SettingsWindow()

            # Save keeps the password EXACTLY as typed. Trimming a credential is how you get an auth
            # failure nobody can explain; config._read_env refuses to do it to a .env password for
            # the same reason. The address IS trimmed — a stray space there is always a slip.
            saved = config.load_config()
            assert saved["proxy_password"] == "  pw with spaces  ", repr(saved["proxy_password"])
            assert saved["proxy_url"] == "other.company.com:3128", repr(saved["proxy_url"])
    finally:
        settings_ui.SettingsWindow._build_ui = real_build
        ctk.CTk.mainloop = real_mainloop
        settings_ui._window_open = False
        config.app_dir = real_app_dir
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            os.environ.pop(key, None)
        gc.collect()
