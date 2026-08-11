"""Music Agent without the GUI — same config, same backends, same actions, no packages at all.

The tray app (main.py) and this share everything below the UI: the config, the two controllers, and
config.ACTIONS. What they don't share is dependencies. Nothing in this file's import graph reaches
customtkinter, pystray, PIL or `keyboard`, and the HTTP underneath is httpmin (stdlib `urllib`), so
requirements-cli.txt installs **nothing** — the interpreter already ships everything this needs. That
is the reason this is a separate entry point rather than a --nogui flag on main.py: a flag would still
have paid for the GUI imports at the top of the module.

Credentials come from a `.env` beside the app and stay there — see config.ENV_FIELDS. Nothing in this
file, and nothing it writes, contains a server address, an account or a key.

    python music_agent_cli.py                          START HERE — stays running, listens for your
                                                       hotkeys until Ctrl+C. Same as `run`.

    python music_agent_cli.py status                   what's configured, and where it came from
    python music_agent_cli.py login | logout           sign in / out of the configured service
    python music_agent_cli.py play | pause | toggle    start, stop, or flip playback
    python music_agent_cli.py next | prev | like | now  one-shot control, no hotkeys involved
    python music_agent_cli.py devices                  Spotify Connect devices this account can see
    python music_agent_cli.py get [KEY] | set KEY VALUE   the config file, field by field
    python music_agent_cli.py hotkeys [ACTION COMBO]   list, or rebind one
    python music_agent_cli.py run                      the hotkey agent itself, headless (Ctrl+C stops)
    python music_agent_cli.py setup                    interactive, for a machine with no .env
    python music_agent_cli.py selftest                 offline checks, no config touched
"""

import argparse
import getpass
import logging
import os
import sys
import time

import config
from cadence import CadenceController, CadenceError, client_from_config
from config import (ACTIONS, DEFAULT_HOTKEYS, DEFAULTS, MODES, config_path, env_config, env_path,
                    load_config, normalize_url, save_config, save_spotify_credentials)

# Config fields that are credentials: `status` and `get` report whether they're set, never what they
# are. A support question should never end with someone pasting their session cookie into a chat.
SECRETS = ("cadence_session", "cf_access_client_secret", "spotify_client_secret",
           "spotify_refresh_token")

# The verbs, and the controller method behind each. `play` and `pause` mean what they say in both
# modes — see CadenceController._to_paused and SpotifyController._transport, which read the current
# state first so that running one twice is a no-op instead of the opposite action. `toggle` is the
# one bound to a hotkey, where flipping IS the wanted behaviour.
VERBS = (
    ("play", "resume", "start playback"),
    ("resume", "resume", "start playback"),
    ("pause", "pause", "stop playback"),
    ("toggle", "play_pause", "flip between playing and paused"),
    ("next", "next_track", "skip to the next track"),
    ("prev", "previous_track", "skip to the previous track"),
    ("like", "toggle_like", "like or unlike the current track"),
    ("now", "show_current", "print what's playing"),
)


def _die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def _ask(prompt, current="", secret=False):
    """Prompt with the current value shown (masked if it's a credential); Enter keeps it."""
    shown = ("set" if current else "") if secret else current
    suffix = f" [{shown}]" if shown else ""
    answer = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return answer or current


def _env_note():
    return f"Edit {env_path() or config.ENV_FILENAME} to change it."


def _redact(url):
    """`http://user:pass@proxy:8080` -> `http://user:***@proxy:8080`.

    Enterprise proxies routinely carry credentials inline, and `status` is the command people paste
    into a chat when asking for help. The host has to stay readable — it is the whole point of the
    line — so only the password goes.
    """
    scheme, _, rest = url.rpartition("://")
    userinfo, at, hostpart = rest.rpartition("@")
    if not at:
        return url
    user = userinfo.partition(":")[0]
    return f"{scheme}://{user}:***@{hostpart}" if scheme else f"{user}:***@{hostpart}"


def _cadence_sign_in(client, cfg, interactive=True):
    """Exchange an account for a session. Uses the `.env` credentials when it has them — that is what
    makes an unattended machine work: drop a .env next to the app and every command below runs without
    anyone typing a password. Falls back to prompting when it doesn't."""
    username, password = cfg.get("cadence_username", ""), cfg.get("cadence_password", "")
    if not (username and password):
        if not interactive:
            _die(f"Not signed in to Cadence, and no USERNAME / PASSWORD in {env_path() or 'a .env'} — "
                 f"run: python music_agent_cli.py login")
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
    try:
        return client.login(username, password)
    except CadenceError as e:
        _die(f"{e}\nCheck DOMAIN / USERNAME / PASSWORD in {env_path() or 'your .env'}.")


def _controller(cfg):
    """The object the actions run against, or exit with the one thing to do about it."""
    if cfg["mode"] == "cadence":
        client = client_from_config(cfg)
        # ponytail: one extra GET per command to find out whether the saved cookie is still good.
        # The alternative — run the command, recognise "session expired", sign in and retry — needs
        # the failure to survive CadenceController._send, which deliberately turns it into a string.
        # Worth doing if the round trip ever becomes the slow part; it isn't, next to Cadence's wake.
        if not client.is_authenticated():
            _cadence_sign_in(client, cfg, interactive=False)
        return CadenceController(client)

    from spotify_backend import SpotifyConfigError, controller_from_config

    try:
        return controller_from_config(cfg)
    except SpotifyConfigError as e:
        _die(f"{e}\nSet SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in {env_path() or 'a .env'}.")


# --------------------------------------------------------------------------------- setup / inspection
def cmd_setup(cfg, args):
    """Interactive setup, for a machine with no `.env`. Anything the .env already supplies is shown
    and skipped — prompting for it would take an answer and then quietly ignore it, because
    save_config keeps those fields out of the config file on purpose."""
    owned = env_config()
    print(f"Config file: {config_path()}")
    if owned:
        print(f"From {env_path()}: {', '.join(sorted(owned))}\n"
              f"Those are read from that file every run — this command won't ask for them.\n")

    print("How do you want to control your music?")
    print("  1) Cadence account — sign in to your Cadence server, nothing else to set up")
    print("  2) Spotify app     — this machine's Spotify Connect device, needs a Client ID + Secret")
    if "mode" in owned:
        print(f"  (MODE is set to {cfg['mode']!r} in the .env — {_env_note()})")
    else:
        choice = input(f"Choice [1-2, currently {cfg['mode']}]: ").strip()
        cfg["mode"] = {"1": "cadence", "2": "spotify"}.get(choice, cfg["mode"])

    if cfg["mode"] == "cadence":
        if "cadence_url" not in owned:
            cfg["cadence_url"] = normalize_url(_ask("Cadence server URL", cfg.get("cadence_url", "")))
        if not cfg["cadence_url"]:
            _die("A Cadence server address is required.")
        if "cf_access_client_id" not in owned and "cf_access_client_secret" not in owned:
            print("\nCloudflare Access service token — only if your server sits behind Access.")
            print("Leave both empty otherwise.")
            cf_id = _ask("  CF-Access-Client-Id", cfg.get("cf_access_client_id", ""))
            cf_secret = _ask("  CF-Access-Client-Secret", cfg.get("cf_access_client_secret", ""),
                             secret=True)
            if bool(cf_id) != bool(cf_secret):
                # Half a token is worse than none: it gets sent, rejected, and looks like a Cadence fault.
                _die("Enter both Cloudflare values, or neither.")
            cfg["cf_access_client_id"], cfg["cf_access_client_secret"] = cf_id, cf_secret
        save_config(cfg)
        return cmd_login(cfg, args)

    if "spotify_client_id" in owned and "spotify_client_secret" in owned:
        print(f"\nSpotify credentials come from {env_path()}.")
    else:
        print("\nCreate an app at developer.spotify.com/dashboard, then paste its keys.")
        client_id = _ask("  Client ID", cfg.get("spotify_client_id", ""))
        client_secret = _ask("  Client Secret", cfg.get("spotify_client_secret", ""), secret=True)
        if not client_id or not client_secret:
            _die("Both the Client ID and the Client Secret are required.")
        redirect = _ask("  Redirect URI",
                        cfg.get("spotify_redirect_uri") or DEFAULTS["spotify_redirect_uri"])
        print(f"\nAdd exactly this redirect URI to the app's settings: {redirect}")
        save_spotify_credentials(cfg, client_id, client_secret, redirect)
        print(f"Saved to {config_path()}")
    cfg["mode"] = "spotify"
    save_config(cfg)

    # Authorise NOW, while the user is here and expecting a browser. Otherwise the first hotkey press
    # is what opens it — and that press blocks the keyboard thread until consent finishes, so all five
    # hotkeys sit dead for up to three minutes with nothing on screen explaining why.
    if input("\nAuthorise with Spotify in your browser now? [Y/n]: ").strip().lower() in ("", "y", "yes"):
        cmd_login(cfg, args)


def cmd_status(cfg, args):
    owned = env_config()

    def source(field):
        return "  (.env)" if field in owned else ""

    print(f"Config file : {config_path()}")
    print(f"Secrets file: {env_path() or '(none — using the config file only)'}")
    # Shown even when unset: on a network with a mandatory proxy the symptom is "cannot resolve the
    # host", which reads as broken DNS, and the first useful question is whether a proxy is in play.
    import urllib.request

    proxies = urllib.request.getproxies()
    from_env = config.apply_env_proxy()
    if proxies:
        where = "  (.env)" if from_env else "  (environment / Windows settings)"
        print(f"Proxy       : {', '.join(f'{k}={_redact(v)}' for k, v in sorted(proxies.items()))}{where}")
    else:
        print("Proxy       : none — connecting directly")
    print(f"Mode        : {cfg['mode']}{source('mode')}")
    if cfg["mode"] == "cadence":
        print(f"Server      : {cfg.get('cadence_url') or '(not set)'}{source('cadence_url')}")
        print(f"Account     : {cfg.get('cadence_username') or '(not set)'}"
              f"{source('cadence_username')}")
        print(f"Session     : {'signed in' if cfg.get('cadence_session') else 'not signed in'}")
        print(f"CF Access   : {'token set' if cfg.get('cf_access_client_id') else 'no token'}"
              f"{source('cf_access_client_id')}")
    else:
        print(f"Client ID   : {cfg.get('spotify_client_id') or '(not set)'}"
              f"{source('spotify_client_id')}")
        print(f"Secret      : {'set' if cfg.get('spotify_client_secret') else '(not set)'}"
              f"{source('spotify_client_secret')}")
        print(f"Redirect URI: {cfg.get('spotify_redirect_uri')}{source('spotify_redirect_uri')}")
        print(f"Authorised  : {'yes' if cfg.get('spotify_refresh_token') else 'no — happens on first use'}")
    print("Hotkeys     :")
    for action_id in ACTIONS:
        print(f"  {cfg['hotkeys'].get(action_id) or '(unbound)':<22} {action_id}"
              f"{'  (.env)' if action_id in owned.get('hotkeys', {}) else ''}")


def cmd_login(cfg, args):
    """Sign in to whichever service `mode` selects — a Cadence account, or Spotify's OAuth consent."""
    if cfg["mode"] == "spotify":
        from spotify_backend import SpotifyConfigError, SpotifyError, controller_from_config

        try:
            controller_from_config(cfg).auth.token()
        except (SpotifyError, SpotifyConfigError) as e:
            _die(str(e))
        print(f"Authorised with Spotify. The refresh token is saved in {config_path()}.")
        return

    if not cfg.get("cadence_url"):
        _die(f"No Cadence server set — put DOMAIN in a .env beside the app, or run: "
             f"python music_agent_cli.py setup")
    me = _cadence_sign_in(client_from_config(cfg), cfg)
    print(f"Signed in as {me.get('username')}. Session saved to {config_path()}")


def cmd_logout(cfg, args):
    if cfg["mode"] == "spotify":
        config.update_config("spotify_refresh_token", "", cfg)
        print("Spotify authorisation cleared — the next command opens the browser again.")
        return
    client_from_config(cfg).forget()
    print("Signed out — the saved Cadence session has been cleared.")


def cmd_get(cfg, args):
    owned = env_config()
    keys = [args.key] if args.key else [k for k in DEFAULTS if k != "hotkeys"]
    for key in keys:
        if key not in DEFAULTS:
            _die(f"Unknown key {key!r}. Known: {', '.join(k for k in DEFAULTS if k != 'hotkeys')}")
        value = cfg.get(key, "")
        shown = ("set" if value else "") if key in SECRETS else value
        print(f"{key} = {shown}{'   (.env)' if key in owned else ''}")


def cmd_set(cfg, args):
    if args.key not in DEFAULTS or args.key == "hotkeys":
        _die(f"Unknown key {args.key!r}. Known: {', '.join(k for k in DEFAULTS if k != 'hotkeys')}")
    if args.key in env_config():
        # Refuse rather than accept-and-drop: save_config keeps .env-supplied fields out of the config
        # file, so this would have printed a confirmation and changed nothing at all.
        _die(f"{args.key} comes from {env_path()} and is not stored here. {_env_note()}")
    value = args.value
    if args.key == "mode" and value not in MODES:
        _die(f"mode must be one of {', '.join(MODES)}")
    if args.key == "cadence_url":
        value = normalize_url(value)
    cfg[args.key] = value
    save_config(cfg)
    print(f"{args.key} = {'set' if args.key in SECRETS and value else value}")


def cmd_hotkeys(cfg, args):
    owned = env_config().get("hotkeys", {})
    if args.action is None:
        for action_id in ACTIONS:
            source = f"   (.env {config.ENV_HOTKEY_PREFIX}{action_id.upper()})" if action_id in owned else ""
            print(f"  {cfg['hotkeys'].get(action_id) or '(unbound)':<22} {action_id}{source}")
        return
    if args.action == "reset":
        cfg["hotkeys"] = dict(DEFAULT_HOTKEYS)
        save_config(cfg)
        if owned:
            print(f"Reset the ones this file owns. {', '.join(sorted(owned))} come from "
                  f"{env_path()} and are unchanged.")
        else:
            print("Hotkeys reset to defaults.")
        return
    if args.action not in ACTIONS:
        _die(f"Unknown action {args.action!r}. Known: {', '.join(ACTIONS)}, or 'reset'.")
    if args.action in owned:
        # Same reason cmd_set refuses: save_config keeps .env-owned shortcuts out of the config file,
        # so this would have printed "next_track -> ctrl+alt+n" and changed nothing at all.
        _die(f"{args.action} is set by {config.ENV_HOTKEY_PREFIX}{args.action.upper()} in "
             f"{env_path()}. {_env_note()}")
    if not args.combo:
        _die(f"Give a combination too, e.g.: python music_agent_cli.py hotkeys {args.action} ctrl+alt+p")
    clash = next((a for a, c in cfg["hotkeys"].items() if c == args.combo and a != args.action), None)
    if clash:
        _die(f"{args.combo!r} is already bound to {clash}.")
    cfg["hotkeys"][args.action] = args.combo
    save_config(cfg)
    print(f"{args.action} -> {args.combo}")


# --------------------------------------------------------------------------------- doing things
def cmd_control(cfg, args):
    # Guarded like main.py's hotkey wrapper and cmd_run: a one-shot command that hits something
    # unexpected should say so in one line, not print a traceback at someone who typed `... next`.
    controller = _controller(cfg)
    try:
        message = getattr(controller, args.method)()
    except Exception as e:  # noqa: BLE001
        _die(f"{args.command} failed: {e}")
    # Both controllers hand back a STRING whether they worked or not — a hotkey wants that, a script
    # cannot read it: "Liked" and "Cadence session expired" are the same shape. last_error is the one
    # bit that tells them apart, and without honouring it here `... play || alert-me` never fires,
    # because the CLI exits 0 on a command that plainly did not happen.
    if controller.last_error:
        _die(controller.last_error)
    if message:
        print(message)


def cmd_devices(cfg, args):
    """Spotify Connect devices this account can see — the 'why won't it play' command."""
    if cfg["mode"] != "spotify":
        _die("devices is Spotify-mode only.")
    try:
        found = _controller(cfg).devices()      # not wrapped in _act — guard it like cmd_control
    except Exception as e:  # noqa: BLE001
        _die(str(e))
    if not found:
        print("No Spotify Connect devices — open Spotify on a phone, desktop or speaker first.")
    for device in found:
        flags = "".join((" (active)" if device.get("is_active") else "",
                         " (restricted — accepts no commands)" if device.get("is_restricted") else ""))
        print(f"  {device.get('name', '<unknown>')!r}{flags}\n      id: {device.get('id')}")


def hotkey_action(controller, method, action_id):
    """One hotkey's callback: run the action and ALWAYS print a line saying what happened.

    Printing unconditionally is the point. Only two of the five actions return any text —
    `show_current` and `toggle_like` — so previously a working `next_track` was indistinguishable
    from a hotkey that never fired at all, and the only way to tell was to go and look at the music.
    A press you cannot see is a press you cannot trust.

    Module level, not a closure inside cmd_run, so the self-check can exercise it without a keyboard.
    """
    def fire():
        stamp = time.strftime("%H:%M:%S")
        try:
            message = getattr(controller, method)()
        except Exception as e:  # noqa: BLE001 — a hotkey must never die on an unexpected error
            print(f"{stamp}  {action_id:<15} FAILED  {e}", file=sys.stderr, flush=True)
            return
        # The controllers return TEXT on failure too, so the text alone can't be trusted to mean
        # success — last_error is what separates "Liked" from "session expired". Same rule as
        # cmd_control, which uses it to pick an exit code.
        if controller.last_error:
            print(f"{stamp}  {action_id:<15} FAILED  {controller.last_error}",
                  file=sys.stderr, flush=True)
        else:
            # flush: stdout is block-buffered when redirected to a file, and an agent someone is
            # tailing to check their keys work must not sit on the answer for 8 KB.
            print(f"{stamp}  {action_id:<15} {message or 'ok'}", flush=True)
    return fire


def cmd_run(cfg, args):
    """The hotkey agent, headless. Win32 RegisterHotKey via winhotkeys — imported here, not at the top,
    because it is the one Windows-only module in this file's graph and every other command runs
    anywhere."""
    import winhotkeys

    controller = _controller(cfg)

    bindings, labels = {}, {}
    for action_id, method in ACTIONS.items():
        combo = cfg["hotkeys"].get(action_id)
        if not combo:
            continue
        bindings[combo] = hotkey_action(controller, method, action_id)
        labels[combo] = action_id
    if not bindings:
        _die("No hotkeys bound — run: python music_agent_cli.py hotkeys")

    for combo, action_id in labels.items():
        print(f"  {combo:<22} {action_id}")
    print(f"\nMusic Agent running in {cfg['mode']} mode. Ctrl+C to stop.")
    print("Every press prints a line below.\n")
    # Anything that fails to register reports here, right after the list above and before the loop
    # blocks — one combination another app already owns must not take the other four with it.
    bound = winhotkeys.listen(bindings, on_error=lambda combo, why: print(
        f"  {combo:<22} NOT bound — {why}", file=sys.stderr))
    if not bound:
        _die("Not one hotkey could be registered. Rebind them: python music_agent_cli.py hotkeys")
    print("\nStopped.")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show the backends' log lines (which request, which device, which token)")
    # No verb runs the hotkey agent. That is what this program IS — you start it, it stays up, it
    # listens. The one-shot verbs are the extra, useful for scripting; making them mandatory meant the
    # obvious command (`python music_agent_cli.py`) printed a usage error at someone whose actual
    # intent was the default behaviour of the whole app.
    parser.set_defaults(handler=cmd_run)
    sub = parser.add_subparsers(dest="command", required=False,
                                title="one-shot commands", metavar="[command]")
    # ...and the ACTION's default, not parser.set_defaults(command=...): argparse applies action
    # defaults first, so the subparsers' own `None` would win and `args.command` would be None.
    sub.default = "run"

    for name, help_text, handler in (
        ("status", "what's configured and where it came from", cmd_status),
        ("login", "sign in to the configured service", cmd_login),
        ("logout", "clear the saved session / authorisation", cmd_logout),
        ("devices", "list Spotify Connect devices", cmd_devices),
        ("run", "the hotkey agent (this is also what happens with no command at all)", cmd_run),
        ("setup", "interactive setup, for a machine with no .env", cmd_setup),
        ("selftest", "offline checks, no config touched", None),
    ):
        sub.add_parser(name, help=help_text).set_defaults(handler=handler)

    get = sub.add_parser("get", help="print one config field, or all of them")
    get.add_argument("key", nargs="?")
    get.set_defaults(handler=cmd_get)

    set_ = sub.add_parser("set", help="set one config field")
    set_.add_argument("key")
    set_.add_argument("value")
    set_.set_defaults(handler=cmd_set)

    hotkeys = sub.add_parser("hotkeys", help="list hotkeys, rebind one, or 'reset'")
    hotkeys.add_argument("action", nargs="?", help=f"one of {', '.join(ACTIONS)}, or 'reset'")
    hotkeys.add_argument("combo", nargs="?", help="e.g. ctrl+alt+p")
    hotkeys.set_defaults(handler=cmd_hotkeys)

    # The actions as bare verbs — the whole point of a CLI is `next` being one word.
    for verb, method, help_text in VERBS:
        sub.add_parser(verb, help=help_text).set_defaults(handler=cmd_control, method=method)
    return parser


def selftest():
    """The things here that fail silently if broken: every subcommand actually resolving to a handler
    and to a method both controllers implement, and the hotkey/config guards refusing bad input
    instead of writing it."""
    import tempfile

    import cadence
    import config as config_module
    import spotify_backend

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

    # cmd_control reads .last_error to pick an exit code; a backend without one would make every
    # command exit 0, including the ones that failed.
    for backend in (cadence.CadenceController, spotify_backend.SpotifyController):
        assert "last_error" in backend.__init__.__code__.co_names, f"{backend.__name__}.last_error"

    # A verb that names a method only ONE backend has is a command that works in one mode and
    # tracebacks in the other — which is exactly what mode-agnostic front ends must not do.
    for _verb, method, _help in VERBS:
        for backend in (cadence.CadenceController, spotify_backend.SpotifyController):
            assert callable(getattr(backend, method, None)), f"{backend.__name__} has no {method}()"
    for method in ACTIONS.values():
        for backend in (cadence.CadenceController, spotify_backend.SpotifyController):
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
            import winhotkeys
            for combo in config_module.DEFAULT_HOTKEYS.values():
                winhotkeys.parse(combo)

    # Every action, and `mode`, must be settable from the .env — that is the promise the docs make.
    # ENV_HOTKEYS is derived from ACTIONS so it cannot drift, but .env.example is hand-written and can:
    # a shortcut nobody documents is one nobody knows exists.
    assert set(config_module.ENV_HOTKEYS.values()) == set(ACTIONS), config_module.ENV_HOTKEYS
    assert "mode" in config_module.ENV_FIELDS.values(), "MODE must be settable from the .env"
    example = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.example"),
                   encoding="utf-8").read()
    undocumented = [k for k in (*config_module.ENV_HOTKEYS, "MODE") if k not in example]
    assert not undocumented, f"not in .env.example: {undocumented}"
    config_module.app_dir = real_app_dir
    print("cli self-check ok")


def main(argv=None):
    # Track names are whatever the artist called them, and both backends' messages contain em dashes.
    # A console on a code page that can't represent one (cp437, cp932, plenty of others) makes print()
    # raise UnicodeEncodeError, which turns "what's playing" into a traceback. Degrade the character,
    # never the command.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = build_parser().parse_args(argv)

    # Quiet by default. Without a handler, logging's last-resort one prints WARNING and above to
    # stderr — so every backend error appeared TWICE: once as `ERROR:root:...` and once as the
    # sentence this file prints itself. -v turns the log back on, at the level that is worth reading.
    logging.basicConfig(level=logging.INFO if args.verbose else logging.CRITICAL,
                        format="%(levelname)s %(message)s")

    if args.command == "selftest":
        return selftest()
    args.handler(load_config(), args)


if __name__ == "__main__":
    main()
