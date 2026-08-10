"""Music Agent without the GUI — same config file, same backends, same five actions.

The tray app (main.py) and this share everything below the UI: cadence_config.txt, the two controllers,
and config.ACTIONS. What they don't share is dependencies — nothing here imports customtkinter, pystray
or PIL, so `pip install -r requirements.txt` (requests + keyboard) is the whole list for this entry
point. That is the reason this file exists rather than a --nogui flag on main.py: a flag would still
have paid for the GUI imports at the top of the module.

    python cli.py setup                          pick a mode and sign in, interactively
    python cli.py status                         what's configured and what isn't
    python cli.py login | logout                 Cadence session
    python cli.py get [KEY] | set KEY VALUE      the config file, field by field
    python cli.py hotkeys [ACTION COMBO]         list, or rebind one
    python cli.py play | next | prev | like | now   one-shot control, no hotkeys involved
    python cli.py devices                        Spotify Connect devices this account can see
    python cli.py run                            the hotkey agent itself, headless (Ctrl+C stops)
"""

import argparse
import getpass
import sys

from cadence import CadenceController, CadenceError, client_from_config, normalize_url
from config import (ACTIONS, DEFAULT_HOTKEYS, DEFAULTS, MODES, config_path, load_config, save_config,
                    save_spotify_credentials)

# Config fields that are credentials: `status` and `get` report whether they're set, never what they
# are. A support question should never end with someone pasting their session cookie into a chat.
SECRETS = ("cadence_session", "cf_access_client_secret", "spotify_client_secret",
           "spotify_refresh_token")


def _die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def _ask(prompt, current="", secret=False):
    """Prompt with the current value shown (masked if it's a credential); Enter keeps it."""
    shown = ("set" if current else "") if secret else current
    suffix = f" [{shown}]" if shown else ""
    answer = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return answer or current


def _controller(cfg):
    """The object the actions run against, or exit with the one thing to do about it."""
    if cfg["mode"] == "cadence":
        client = client_from_config(cfg)
        if not client.is_authenticated():
            _die("Not signed in to Cadence — run: python cli.py login")
        return CadenceController(client)

    from spotify_backend import SpotifyConfigError, controller_from_config

    try:
        return controller_from_config(cfg)
    except SpotifyConfigError as e:
        _die(f"{e}\nRun: python cli.py setup")


# --------------------------------------------------------------------------------- setup / inspection
def cmd_setup(cfg, args):
    print(f"Config file: {config_path()}\n")
    print("How do you want to control your music?")
    print("  1) Cadence account — sign in to your Cadence server, nothing else to set up")
    print("  2) Spotify app     — this machine's Spotify Connect device, needs a Client ID + Secret")
    choice = input(f"Choice [1-2, currently {cfg['mode']}]: ").strip()
    cfg["mode"] = {"1": "cadence", "2": "spotify"}.get(choice, cfg["mode"])

    if cfg["mode"] == "cadence":
        cfg["cadence_url"] = normalize_url(_ask("Cadence server URL", cfg.get("cadence_url", "")))
        if not cfg["cadence_url"]:
            _die("A Cadence server address is required.")
        print("\nCloudflare Access service token — only if your server sits behind Access.")
        print("Leave both empty otherwise.")
        cf_id = _ask("  CF-Access-Client-Id", cfg.get("cf_access_client_id", ""))
        cf_secret = _ask("  CF-Access-Client-Secret", cfg.get("cf_access_client_secret", ""), secret=True)
        if bool(cf_id) != bool(cf_secret):
            # Half a token is worse than none: it gets sent, rejected, and looks like a Cadence fault.
            _die("Enter both Cloudflare values, or neither.")
        cfg["cf_access_client_id"], cfg["cf_access_client_secret"] = cf_id, cf_secret
        save_config(cfg)
        return cmd_login(cfg, args)

    print("\nCreate an app at developer.spotify.com/dashboard, then paste its keys.")
    client_id = _ask("  Client ID", cfg.get("spotify_client_id", ""))
    client_secret = _ask("  Client Secret", cfg.get("spotify_client_secret", ""), secret=True)
    if not client_id or not client_secret:
        _die("Both the Client ID and the Client Secret are required.")
    redirect = _ask("  Redirect URI", cfg.get("spotify_redirect_uri") or DEFAULTS["spotify_redirect_uri"])
    print(f"\nAdd exactly this redirect URI to the app's settings: {redirect}")
    cfg["mode"] = "spotify"
    save_spotify_credentials(cfg, client_id, client_secret, redirect)
    print(f"Saved to {config_path()}")

    # Authorise NOW, while the user is here and expecting a browser. Otherwise the first hotkey press
    # is what opens it — and that press blocks the keyboard thread until consent finishes, so all five
    # hotkeys sit dead for up to three minutes with nothing on screen explaining why.
    if input("\nAuthorise with Spotify in your browser now? [Y/n]: ").strip().lower() in ("", "y", "yes"):
        from spotify_backend import SpotifyError, controller_from_config

        try:
            controller_from_config(cfg).auth.token()
        except SpotifyError as e:
            _die(f"{e}\nCredentials are saved — run `python cli.py setup` again to retry.")
        print("Authorised. `python cli.py run` will pick it up from here.")


def cmd_status(cfg, args):
    print(f"Config file : {config_path()}")
    print(f"Mode        : {cfg['mode']}")
    if cfg["mode"] == "cadence":
        print(f"Server      : {cfg.get('cadence_url') or '(not set)'}")
        print(f"Session     : {'signed in' if cfg.get('cadence_session') else 'not signed in'}")
        print(f"CF Access   : {'token set' if cfg.get('cf_access_client_id') else 'no token'}")
    else:
        print(f"Client ID   : {cfg.get('spotify_client_id') or '(not set)'}")
        print(f"Secret      : {'set' if cfg.get('spotify_client_secret') else '(not set)'}")
        print(f"Redirect URI: {cfg.get('spotify_redirect_uri')}")
        print(f"Authorised  : {'yes' if cfg.get('spotify_refresh_token') else 'no — happens on first use'}")
    print("Hotkeys     :")
    for action_id in ACTIONS:
        print(f"  {cfg['hotkeys'].get(action_id) or '(unbound)':<22} {action_id}")


def cmd_login(cfg, args):
    if cfg["mode"] != "cadence":
        _die("login is for Cadence mode. Spotify credentials are set with: python cli.py setup")
    if not cfg.get("cadence_url"):
        _die("No Cadence server set — run: python cli.py setup")
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    try:
        me = client_from_config(cfg).login(username, password)
    except CadenceError as e:
        _die(str(e))
    print(f"Signed in as {me.get('username')}. Session saved to {config_path()}")


def cmd_logout(cfg, args):
    client_from_config(cfg).forget()
    print("Signed out — the saved Cadence session has been cleared.")


def cmd_get(cfg, args):
    keys = [args.key] if args.key else [k for k in DEFAULTS if k != "hotkeys"]
    for key in keys:
        if key not in DEFAULTS:
            _die(f"Unknown key {key!r}. Known: {', '.join(k for k in DEFAULTS if k != 'hotkeys')}")
        value = cfg.get(key, "")
        print(f"{key} = {('set' if value else '') if key in SECRETS else value}")


def cmd_set(cfg, args):
    if args.key not in DEFAULTS or args.key == "hotkeys":
        _die(f"Unknown key {args.key!r}. Known: {', '.join(k for k in DEFAULTS if k != 'hotkeys')}")
    value = args.value
    if args.key == "mode" and value not in MODES:
        _die(f"mode must be one of {', '.join(MODES)}")
    if args.key == "cadence_url":
        value = normalize_url(value)
    cfg[args.key] = value
    save_config(cfg)
    print(f"{args.key} = {'set' if args.key in SECRETS and value else value}")


def cmd_hotkeys(cfg, args):
    if args.action is None:
        for action_id in ACTIONS:
            print(f"  {cfg['hotkeys'].get(action_id) or '(unbound)':<22} {action_id}")
        return
    if args.action == "reset":
        cfg["hotkeys"] = dict(DEFAULT_HOTKEYS)
        save_config(cfg)
        print("Hotkeys reset to defaults.")
        return
    if args.action not in ACTIONS:
        _die(f"Unknown action {args.action!r}. Known: {', '.join(ACTIONS)}, or 'reset'.")
    if not args.combo:
        _die(f"Give a combination too, e.g.: python cli.py hotkeys {args.action} ctrl+alt+p")
    clash = next((a for a, c in cfg["hotkeys"].items() if c == args.combo and a != args.action), None)
    if clash:
        _die(f"{args.combo!r} is already bound to {clash}.")
    cfg["hotkeys"][args.action] = args.combo
    save_config(cfg)
    print(f"{args.action} -> {args.combo}")


# --------------------------------------------------------------------------------- doing things
def cmd_control(cfg, args):
    # Guarded like main.py's hotkey wrapper and cmd_run: a one-shot command that hits something
    # unexpected should say so in one line, not print a traceback at someone who typed `cli.py next`.
    try:
        message = getattr(_controller(cfg), ACTIONS[args.action_id])()
    except Exception as e:  # noqa: BLE001
        _die(f"{args.action_id} failed: {e}")
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
    for device in found:
        flags = "".join((" (active)" if device.get("is_active") else "",
                         " (restricted — accepts no commands)" if device.get("is_restricted") else ""))
        print(f"  {device.get('name', '<unknown>')!r}{flags}\n      id: {device.get('id')}")


def cmd_run(cfg, args):
    import keyboard

    controller = _controller(cfg)

    def bind(method, action_id):
        def fire():
            try:
                message = getattr(controller, method)()
            except Exception as e:  # noqa: BLE001 — a hotkey must never die on an unexpected error
                print(f"{action_id} failed: {e}", file=sys.stderr)
                return
            if message:
                print(message)
        return fire

    bound = 0
    for action_id, method in ACTIONS.items():
        combo = cfg["hotkeys"].get(action_id)
        if not combo:
            continue
        try:
            keyboard.add_hotkey(combo, bind(method, action_id))
        except Exception as e:  # noqa: BLE001 — one bad combo must not take the other four with it
            print(f"Could not register {combo!r} for {action_id}: {e}", file=sys.stderr)
            continue
        print(f"  {combo:<22} {action_id}")
        bound += 1
    if not bound:
        _die("No hotkeys bound — run: python cli.py hotkeys")
    print(f"\nMusic Agent running in {cfg['mode']} mode. Ctrl+C to stop.")
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\nStopped.")



def build_parser():
    parser = argparse.ArgumentParser(prog="cli.py", description=__doc__.split("\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text, handler in (
        ("setup", "pick a mode and sign in, interactively", cmd_setup),
        ("status", "what's configured and what isn't", cmd_status),
        ("login", "sign in to Cadence", cmd_login),
        ("logout", "clear the saved Cadence session", cmd_logout),
        ("devices", "list Spotify Connect devices", cmd_devices),
        ("run", "run the hotkey agent headless", cmd_run),
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

    # The five actions as bare verbs — the whole point of a CLI is `cli.py next` being one word.
    for verb, action_id in (("play", "play_pause"), ("pause", "play_pause"), ("next", "next_track"),
                            ("prev", "previous_track"), ("like", "like_unlike"), ("now", "show_current")):
        sub.add_parser(verb, help=f"{action_id.replace('_', ' ')} once").set_defaults(
            handler=cmd_control, action_id=action_id)
    return parser


def selftest():
    """The two things here that fail silently if broken: every subcommand actually resolving to a
    handler, and the hotkey/config guards refusing bad input instead of writing it."""
    import tempfile
    import config as config_module

    parser = build_parser()
    for verb in ("setup", "status", "login", "logout", "devices", "run", "get", "hotkeys",
                 "play", "pause", "next", "prev", "like", "now"):
        args = parser.parse_args([verb])
        assert callable(args.handler), verb
    assert parser.parse_args(["next"]).action_id == "next_track"
    assert parser.parse_args(["pause"]).action_id == "play_pause"
    assert set(ACTIONS) == set(DEFAULT_HOTKEYS), "every action needs a default hotkey"

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
    config_module.app_dir = real_app_dir
    print("cli self-check ok")


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "selftest":
        return selftest()
    args.handler(load_config(), args)


if __name__ == "__main__":
    main()
