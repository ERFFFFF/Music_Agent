"""Checks for `music_agent.cli` — moved here from the module's own selftest()."""

from music_agent.cli import *  # noqa: F401,F403 — the public surface under test
from music_agent.cli import (  # noqa: F401 — including the private names it exercises
    ACTIONS, DEFAULT_HOTKEYS, MODES, SECRETS, SHOWN_ANYWAY, VERBS, build_parser, cmd_hotkeys, cmd_run, cmd_set, config, hotkey_action, load_config, os)
import music_agent.cli as MODULE


def test_cli():
    """The things here that fail silently if broken: every subcommand actually resolving to a handler
    and to a method both controllers implement, and the hotkey/config guards refusing bad input
    instead of writing it."""
    import tempfile

    from music_agent.backends import cadence
    from music_agent import config as config_module
    from music_agent.backends import spotify

    config_module.use_env(True)      # what cli.main() does at startup, and only this front end does

    parser = build_parser()
    for verb, _help, _handler in (("status", 0, 0), ("login", 0, 0), ("logout", 0, 0),
                                  ("devices", 0, 0), ("run", 0, 0), ("setup", 0, 0),
                                  ("get", 0, 0), ("hotkeys", 0, 0)):
        assert callable(parser.parse_args([verb]).handler), verb
    assert parser.parse_args(["next"]).method == "next_track"
    assert parser.parse_args(["pause"]).method == "pause"
    assert parser.parse_args(["play"]).method == "resume"
    # No verb must launch the agent, not print a usage error. This is the program's default job and
    # the reason it exists; a `required=True` subparser turned that into "the one thing you can't do".
    assert parser.parse_args([]).handler is cmd_run, "a bare invocation must run the hotkey agent"

    # EVERY press must print, including the three actions whose controller returns None. Without this
    # a working `next_track` looks exactly like a hotkey that never fired.
    import contextlib
    import io as _io

    class FakeController:
        last_error = None

        def next_track(self):
            return None                       # the silent-on-success case

        def toggle_like(self):
            return "Liked"

        def boom(self):
            raise RuntimeError("kaboom")

    fake = FakeController()
    for method, action, expect in (("next_track", "next_track", "ok"),
                                   ("toggle_like", "like_unlike", "Liked")):
        out = _io.StringIO()
        with contextlib.redirect_stdout(out):
            hotkey_action(fake, method, action)()
        assert action in out.getvalue() and expect in out.getvalue(), out.getvalue()

    err = _io.StringIO()                       # a failure goes to stderr, marked, never silent
    fake.last_error = "No Cadence tab is open"
    with contextlib.redirect_stderr(err):
        hotkey_action(fake, "next_track", "next_track")()
    assert "FAILED" in err.getvalue() and "No Cadence tab" in err.getvalue(), err.getvalue()

    err = _io.StringIO()                       # ...and so does an unexpected exception
    with contextlib.redirect_stderr(err):
        hotkey_action(fake, "boom", "play_pause")()
    assert "FAILED" in err.getvalue() and "kaboom" in err.getvalue(), err.getvalue()
    assert parser.parse_args([]).command == "run"
    assert parser.parse_args(["-v"]).handler is cmd_run and parser.parse_args(["-v"]).verbose
    assert not parser.parse_args(["now"]).verbose and parser.parse_args(["-v", "now"]).verbose
    assert set(ACTIONS) == set(DEFAULT_HOTKEYS), "every action needs a default hotkey"

    # Every credential sealed on disk is masked when printed, unless it is deliberately listed as
    # safe to show. The hand-written list this replaced fell behind: proxy_password reached
    # SECRET_FIELDS and `get` printed it in the clear.
    for field in config_module.SECRET_FIELDS:
        assert field in SECRETS or field in SHOWN_ANYWAY, f"{field} is neither masked nor allow-listed"
    assert "proxy_password" in SECRETS and "cadence_session" in SECRETS

    # cmd_control reads .last_error to pick an exit code; a backend without one would make every
    # command exit 0, including the ones that failed.
    for backend in (cadence.CadenceController, spotify.SpotifyController):
        assert "last_error" in backend.__init__.__code__.co_names, f"{backend.__name__}.last_error"

    # A verb that names a method only ONE backend has is a command that works in one mode and
    # tracebacks in the other — which is exactly what mode-agnostic front ends must not do.
    for _verb, method, _help in VERBS:
        for backend in (cadence.CadenceController, spotify.SpotifyController):
            assert callable(getattr(backend, method, None)), f"{backend.__name__} has no {method}()"
    for method in ACTIONS.values():
        for backend in (cadence.CadenceController, spotify.SpotifyController):
            assert callable(getattr(backend, method, None)), f"{backend.__name__} has no {method}()"

    real_app_dir = config_module.app_dir
    with tempfile.TemporaryDirectory() as d:
        config_module.app_dir = lambda: d
        cfg = load_config()

        cmd_hotkeys(cfg, parser.parse_args(["hotkeys", "play_pause", "ctrl+alt+p"]))
        assert load_config()["hotkeys"]["play_pause"] == "ctrl+alt+p"

        for bad in (["hotkeys", "nonsense", "ctrl+a"],       # unknown action
                    ["hotkeys", "next_track"],               # no combination given
                    ["hotkeys", "next_track", "ctrl+alt+p"]):  # already bound to play_pause
            try:
                cmd_hotkeys(load_config(), parser.parse_args(bad))
                raise AssertionError(f"{bad} should have been refused")
            except SystemExit:
                pass
        assert load_config()["hotkeys"]["next_track"] == DEFAULT_HOTKEYS["next_track"]

        cmd_hotkeys(load_config(), parser.parse_args(["hotkeys", "reset"]))
        assert load_config()["hotkeys"] == DEFAULT_HOTKEYS

        cmd_set(load_config(), parser.parse_args(["set", "cadence_url", "cadence.example.com"]))
        assert load_config()["cadence_url"] == "https://cadence.example.com", "bare host must get https"
        for bad in (["set", "mode", "nonsense"], ["set", "not_a_key", "x"], ["set", "hotkeys", "x"]):
            try:
                cmd_set(load_config(), parser.parse_args(bad))
                raise AssertionError(f"{bad} should have been refused")
            except SystemExit:
                pass
        assert load_config()["mode"] in MODES

        # Anything the .env owns must be REFUSED here, not accepted and silently dropped by
        # save_config — a command that prints a confirmation and changes nothing is the worst of both.
        with open(config_module.env_path() or f"{d}/{config_module.ENV_FILENAME}", "w") as f:
            f.write("DOMAIN=env.example.com\nHOTKEY_NEXT_TRACK=ctrl+shift+n\n")
        try:
            cmd_set(load_config(), parser.parse_args(["set", "cadence_url", "typed.example.com"]))
            raise AssertionError("setting an .env-owned field should have been refused")
        except SystemExit:
            pass
        assert load_config()["cadence_url"] == "https://env.example.com"

        assert load_config()["hotkeys"]["next_track"] == "ctrl+shift+n", "the .env must set it"
        try:
            cmd_hotkeys(load_config(), parser.parse_args(["hotkeys", "next_track", "ctrl+alt+n"]))
            raise AssertionError("rebinding an .env-owned shortcut should have been refused")
        except SystemExit:
            pass
        assert load_config()["hotkeys"]["next_track"] == "ctrl+shift+n"
        # ...while an action the .env does NOT name is still freely rebindable
        cmd_hotkeys(load_config(), parser.parse_args(["hotkeys", "show_current", "ctrl+alt+n"]))
        assert load_config()["hotkeys"]["show_current"] == "ctrl+alt+n"

        # every shortcut the .env can set must be one winhotkeys can actually parse, or `run` binds
        # nothing and only says so at launch
        if os.name == "nt":
            from music_agent.win32 import hotkeys
            for combo in config_module.DEFAULT_HOTKEYS.values():
                hotkeys.parse(combo)

    config_module.app_dir = real_app_dir     # restore FIRST: the checks below read the real project

    # Every action, and `mode`, must be settable from the .env — that is the promise the docs make.
    # ENV_HOTKEYS is derived from ACTIONS so it cannot drift, but .env.example is hand-written and can:
    # a shortcut nobody documents is one nobody knows exists.
    assert set(config_module.ENV_HOTKEYS.values()) == set(ACTIONS), config_module.ENV_HOTKEYS
    assert "mode" in config_module.ENV_FIELDS.values(), "MODE must be settable from the .env"
    # app_dir() already knows where the project root is, frozen or not. Recomputing it from __file__
    # is what broke when this module moved into a package, so ask the one function that knows.
    example = open(os.path.join(config.app_dir(), ".env.example"), encoding="utf-8").read()
    undocumented = [k for k in (*config_module.ENV_HOTKEYS, "MODE") if k not in example]
    assert not undocumented, f"not in .env.example: {undocumented}"


def test_logout_really_logs_out():
    """`logout` has to leave the app UNCONFIGURED, not just cookieless.

    The account is stored now (the tray app's sign-in window writes it), and `_controller` signs
    itself back in from a stored username/password without asking. So clearing only the session
    would print "Signed out" and leave the very next verb signed in — the same trap the GUI's Sign
    out had. Found by review, pinned here.
    """
    import contextlib
    import io
    import os
    import tempfile

    from music_agent import config as config_module

    real_app_dir = config_module.app_dir
    try:
        with tempfile.TemporaryDirectory() as d:
            config_module.app_dir = lambda: d
            config_module.use_env(False)
            cfg = config_module.load_config()
            cfg.update({"mode": "cadence", "cadence_url": "https://cadence.example",
                        "cadence_session": "a-cookie", "cadence_username": "someone",
                        "cadence_password": "stored-pw"})
            config_module.save_config(cfg)
            assert config_module.is_configured(config_module.load_config())

            with contextlib.redirect_stdout(io.StringIO()):
                MODULE.cmd_logout(config_module.load_config(), None)

            after = config_module.load_config()
            assert after["cadence_session"] == "", "the cookie survived"
            assert after["cadence_username"] == "" and after["cadence_password"] == "", after
            assert not config_module.is_configured(after), "still configured: logout signed nothing out"

            # A .env-supplied account is NOT this command's to clear — but staying quiet about it
            # would look like the sign-out failed when the next verb logs straight back in.
            with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
                f.write("DOMAIN=cadence.example\nUSERNAME=someone\nPASSWORD=env-pw\n")
            config_module.use_env(True)
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                MODULE.cmd_logout(config_module.load_config(), None)
            assert "will sign in again" in printed.getvalue(), printed.getvalue()
    finally:
        config_module.app_dir = real_app_dir
        config_module.use_env(True)
