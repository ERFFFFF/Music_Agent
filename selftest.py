"""One command to check the whole app: `python selftest.py`.

Runs each module's own self-check, then the three things no single module can check by itself:

  1. that every name used in the GUI modules actually resolves — they only ever run on Windows with
     customtkinter/pystray/PIL installed, so a missing import there is invisible until launch day;
  2. the Cadence login round trip, through the config file and back, as a restart would do it;
  3. the Spotify OAuth redirect, against a real loopback socket.

No test framework: asserts and a pass/fail line, same as the module self-checks. Everything is offline
— the two logins run against a scripted transport, never a real server.
"""

import builtins
import json
import os
import symtable
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except Exception as e:
        FAILURES.append(name)
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------------- fake HTTP replies
class _Resp(SimpleNamespace):
    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def reply(status=200, payload=None, body=None, url="https://cadence.example/x"):
    raw = body if body is not None else (json.dumps(payload).encode() if payload is not None else b"")
    return _Resp(status_code=status, content=raw, headers={}, url=url, history=(), _json=payload)


# --------------------------------------------------------------------------------- 1. names resolve
def names_resolve():
    """Catch `os.path.join(...)` left behind after someone deleted `import os`.

    Uses the compiler's own symbol table: any name a function only READS, that resolves globally and
    that the module never binds, is a NameError waiting for the right code path. main/login_ui/
    settings_ui are the ones that matter — they cannot be imported off Windows, so nothing else here
    would ever notice.
    """
    known_globals = {"__file__", "__name__", "__doc__"}
    problems = []
    for name in ("main", "cli", "config", "cadence", "spotify_backend", "login_ui", "settings_ui"):
        path = os.path.join(HERE, name + ".py")
        top = symtable.symtable(open(path).read(), path, "exec")
        bound = {s.get_name() for s in top.get_symbols()
                 if s.is_assigned() or s.is_imported() or s.is_parameter() or s.is_namespace()}

        def walk(table):
            for s in table.get_symbols():
                if s.is_global() and s.is_referenced() and not s.is_assigned():
                    if s.get_name() not in bound | known_globals | set(dir(builtins)):
                        problems.append(f"{name}.py: {s.get_name()!r} in {table.get_name()}()")
            for child in table.get_children():
                walk(child)

        walk(top)
    assert not problems, "undefined names: " + ", ".join(problems)


# --------------------------------------------------------------------------------- 2. Cadence login
def cadence_login():
    """Sign in, restart, still signed in, sign out — the path a user actually walks."""
    import cadence
    import config

    def transport(self, method, url, **kw):
        if url.endswith("/api/login"):
            self.cookies.set("session", "server-cookie", domain="cadence.example", path="/")
            return reply(200, {"ok": True})
        if url.endswith("/api/api/me"):
            if not any(c.name == "session" for c in self.cookies):
                return reply(401, None, b"")
            return reply(200, {"username": "bob"})
        if url.endswith("/api/remote/command"):
            return reply(200, {"live": True})
        raise AssertionError(f"unexpected request: {method} {url}")

    real_app_dir = config.app_dir
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            cfg = config.load_config()
            cfg["cadence_url"] = "cadence.example"        # bare host — must gain https on its own
            config.save_config(cfg)   # every real caller saves before signing in; update_config does
                                      # not resurrect unsaved edits, and must not
            with mock.patch("requests.Session.request", transport):
                client = cadence.client_from_config(cfg)
                assert client.base_url == "https://cadence.example", client.base_url
                assert client.login("bob", "hunter2")["username"] == "bob"

                # the session has to reach DISK, or the next launch asks for the password again
                assert json.load(open(config.config_path()))["cadence_session"] == "server-cookie"
                assert config.is_configured(config.load_config())

                # ...and a fresh process must come back signed in, with exactly one cookie in the jar
                # (two named `session` is the bug that once killed every hotkey on second launch)
                restarted = cadence.client_from_config(config.load_config())
                assert restarted.is_authenticated(), "a saved session must authenticate on restart"
                assert len(restarted.session.cookies) == 1
                assert cadence.CadenceController(restarted).play_pause() is None

                restarted.forget()
            assert json.load(open(config.config_path()))["cadence_session"] == "", \
                "signing out must clear the token on disk, not just in memory"
    finally:
        config.app_dir = real_app_dir


def cadence_login_refused():
    """A wrong password must not leave anything behind."""
    import cadence
    import config

    real_app_dir = config.app_dir
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            cfg = config.load_config()
            cfg["cadence_url"] = "https://cadence.example"
            with mock.patch("requests.Session.request", lambda *a, **k: reply(401, None, b"")):
                try:
                    cadence.client_from_config(cfg).login("bob", "wrong")
                    raise AssertionError("a 401 must raise, not return")
                except cadence.CadenceError:
                    pass
            assert config.load_config()["cadence_session"] == ""
    finally:
        config.app_dir = real_app_dir


def credential_write_does_not_clobber():
    """A long-lived client persisting a credential must not revert what Settings saved meanwhile.

    Both backends hold the config dict they were constructed with and write it back much later — the
    Cadence cookie slides on every command, the Spotify refresh token rotates. Writing that captured
    dict wholesale silently undid any hotkey or mode saved in between. This is the regression test.
    """
    import cadence
    import config
    import spotify_backend

    real_app_dir = config.app_dir
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            cfg = config.load_config()
            cfg["cadence_url"] = "https://cadence.example"
            config.save_config(cfg)

            client = cadence.client_from_config(cfg)          # captures cfg as it is now
            spotify = spotify_backend.controller_from_config(
                {**cfg, "spotify_client_id": "cid", "spotify_client_secret": "cs"})

            # ...meanwhile the Settings window saves a new hotkey and a new mode
            later = config.load_config()
            later["hotkeys"]["play_pause"] = "f9"
            later["mode"] = "spotify"
            config.save_config(later)

            client._emit("rotated-cookie")                    # Cadence re-issued the session
            spotify.auth.on_token("rotated-refresh")          # Spotify rotated the refresh token

            on_disk = config.load_config()
            assert on_disk["hotkeys"]["play_pause"] == "f9", "a credential write reverted the hotkey"
            assert on_disk["mode"] == "spotify", "a credential write reverted the mode"
            assert on_disk["cadence_session"] == "rotated-cookie"
            assert on_disk["spotify_refresh_token"] == "rotated-refresh"
    finally:
        config.app_dir = real_app_dir


def credentials_encrypted_at_rest():
    """Every credential really is sealed on the way out — including the ones added since.

    DPAPI is a no-op off Windows (`_dpapi` returns None), so nothing else here exercises the encrypt/
    decrypt path at all. This stands in a reversible fake for it, which is enough to prove the wiring:
    that SECRET_FIELDS covers every credential in DEFAULTS, that save_config seals all of them, that
    load_config unseals them, and that mode/hotkeys deliberately stay readable.
    """
    import config

    # Any field holding a credential must be in SECRET_FIELDS. Named explicitly rather than pattern-
    # matched, so adding one to DEFAULTS and forgetting to seal it fails right here.
    credentials = {"cadence_url", "cadence_session", "cf_access_client_id", "cf_access_client_secret",
                   "spotify_client_id", "spotify_client_secret", "spotify_refresh_token"}
    assert credentials <= set(config.SECRET_FIELDS), \
        f"not sealed at rest: {sorted(credentials - set(config.SECRET_FIELDS))}"
    assert "hotkeys" not in config.SECRET_FIELDS and "mode" not in config.SECRET_FIELDS

    fake = lambda protect, data: (data[::-1] if protect else data[::-1])   # noqa: E731 — reversible
    real_app_dir = config.app_dir
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            with mock.patch.object(config, "_dpapi", fake):
                cfg = config.load_config()
                # distinctive values: a plain word like "refresh" would also match the KEY name
                cfg.update({"cadence_session": "SESSION-VALUE-A", "mode": "spotify",
                            "spotify_refresh_token": "REFRESHTOKEN-VALUE-B",
                            "spotify_client_secret": "CLIENTSECRET-VALUE-C"})
                cfg["hotkeys"]["play_pause"] = "f8"
                config.save_config(cfg)

                raw = json.load(open(config.config_path()))
                for key in config.SECRET_FIELDS:
                    if raw[key]:
                        assert raw[key].startswith(config.ENC_PREFIX), f"{key} written in the clear"
                for secret in ("SESSION-VALUE-A", "REFRESHTOKEN-VALUE-B", "CLIENTSECRET-VALUE-C"):
                    assert secret not in open(config.config_path()).read(), f"{secret!r} is on disk"
                assert raw["mode"] == "spotify" and raw["hotkeys"]["play_pause"] == "f8"

                back = config.load_config()
                assert back["cadence_session"] == "SESSION-VALUE-A"
                assert back["spotify_refresh_token"] == "REFRESHTOKEN-VALUE-B"
                assert back["spotify_client_secret"] == "CLIENTSECRET-VALUE-C"

                # update_config has to survive the round trip too — it reads, sets one field, writes
                config.update_config("spotify_refresh_token", "rotated", back)
                assert config.load_config()["spotify_refresh_token"] == "rotated"
                assert config.load_config()["cadence_session"] == "SESSION-VALUE-A", "clobbered a sibling"
                assert json.load(open(config.config_path()))["spotify_refresh_token"].startswith(
                    config.ENC_PREFIX), "update_config wrote a credential in the clear"
    finally:
        config.app_dir = real_app_dir


def corrupt_config_never_raises():
    """load_config's contract: any garbage on disk yields defaults rather than stopping the launch."""
    import config

    real_app_dir = config.app_dir
    try:
        for junk in ('{"hotkeys": "ctrl+a"}', '{"hotkeys": [1,2]}', '{"hotkeys": {"play_pause": null}}',
                     '{"mode": null}', '{ not json', '[]', 'null', ''):
            with tempfile.TemporaryDirectory() as d:
                config.app_dir = lambda: d
                open(os.path.join(d, config.CONFIG_FILENAME), "w").write(junk)
                cfg = config.load_config()
                assert cfg["mode"] in config.MODES, junk
                assert set(cfg["hotkeys"]) >= set(config.ACTIONS), junk
                assert all(isinstance(v, str) for v in cfg["hotkeys"].values()), junk
    finally:
        config.app_dir = real_app_dir


# --------------------------------------------------------------------------------- 3. Spotify OAuth
def spotify_consent(port=8899, wrong_state=False):
    """Drive the real local callback server: browser -> redirect -> code -> token."""
    import spotify_backend

    saved = []
    auth = spotify_backend._Auth("cid", "csecret", f"http://127.0.0.1:{port}/callback", "", saved.append)
    opened = {}

    def browser(url):
        opened["url"] = url
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]

        def redirect():
            time.sleep(0.2)
            for target in (f"http://127.0.0.1:{port}/favicon.ico",           # browsers ask for this
                           f"http://127.0.0.1:{port}/callback?code=AUTHCODE"
                           f"&state={'wrong' if wrong_state else state}"):
                try:
                    urllib.request.urlopen(target, timeout=5).read()
                except Exception:
                    pass
        threading.Thread(target=redirect, daemon=True).start()
        return True

    token = {"access_token": "AT", "expires_in": 3600, "refresh_token": "RT"}
    with mock.patch("webbrowser.open", browser), \
         mock.patch("requests.post", return_value=reply(200, token)) as post:
        if wrong_state:
            try:
                auth.token()
                raise AssertionError("a mismatched state must be refused")
            except spotify_backend.SpotifyError as e:
                assert "security check" in str(e), str(e)
            return
        assert auth.token() == "AT"

    assert auth.refresh_token == "RT" and saved == ["RT"], "the refresh token must be handed upstream"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(opened["url"]).query)
    assert query["response_type"] == ["code"] and query["client_id"] == ["cid"]
    body = post.call_args.kwargs["data"]
    assert body["grant_type"] == "authorization_code" and body["code"] == "AUTHCODE"
    assert "client_secret" not in body, "credentials go in the Basic header, never the body"
    assert post.call_args.kwargs["headers"]["Authorization"].startswith("Basic ")


# --------------------------------------------------------------------------------- run everything
def main():
    print("module self-checks")
    import cadence
    import cli
    import config
    import spotify_backend
    for name, fn in (("config", config.demo), ("cadence", cadence.selftest),
                     ("spotify_backend", spotify_backend.selftest), ("cli", cli.selftest)):
        check(name, fn)

    print("\ncross-module")
    check("every name in every module resolves", names_resolve)
    check("cadence: login, restart, sign out", cadence_login)
    check("cadence: a wrong password saves nothing", cadence_login_refused)
    check("a credential write never reverts a settings save", credential_write_does_not_clobber)
    check("every credential is encrypted at rest", credentials_encrypted_at_rest)
    check("a corrupt config yields defaults, never a crash", corrupt_config_never_raises)
    check("spotify: consent on a real socket", lambda: spotify_consent(8899))
    check("spotify: a mismatched state is refused", lambda: spotify_consent(8902, wrong_state=True))
    check("spotify: the port is free for a second attempt", lambda: spotify_consent(8899))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("everything passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
