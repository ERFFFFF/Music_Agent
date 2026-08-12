"""The checks no single module can make about itself.

  1. that every name used in the GUI modules actually resolves — they only ever run on Windows with
     customtkinter/pystray/PIL installed, so a missing import there is invisible until launch day;
  2. the Cadence login round trip, through the config file and back, as a restart would do it;
  3. the Spotify OAuth redirect, against a real loopback socket.

Offline: both logins run against a scripted transport, never a real server. The per-module checks live
beside these, one tests/test_<module>.py each.
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

# The PACKAGE directory, asked of the package itself rather than derived from this file's location —
# tests/ is no longer next to the code, and `src/` put another level in between. An installed copy
# answers this correctly too, which is the point of testing what was installed.
import music_agent

PACKAGE = os.path.dirname(os.path.abspath(music_agent.__file__))

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
def test_names_resolve():
    """Catch `os.path.join(...)` left behind after someone deleted `import os`.

    Uses the compiler's own symbol table: any name a function only READS, that resolves globally and
    that the module never binds, is a NameError waiting for the right code path. ui/tray.py,
    ui/login.py and ui/settings.py are the ones that matter — they cannot be imported off Windows, so
    nothing else here would ever notice.
    """
    known_globals = {"__file__", "__name__", "__doc__"}
    problems = []
    # Walk the PACKAGE rather than a hand-kept list -- a module added later is checked without
    # anyone remembering to add it here, which is the only way this stays true.
    for path in sorted(os.path.join(root, f) for root, _d, fs in os.walk(PACKAGE)
                       for f in fs if f.endswith(".py")):
        name = os.path.relpath(path, PACKAGE).replace(os.sep, ".")[:-3]
        # encoding= is not optional: these files are UTF-8 and Windows would otherwise decode them
        # with cp1252, where this check dies on the first em dash in a comment.
        top = symtable.symtable(open(path, encoding="utf-8").read(), path, "exec")
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
def test_cadence_login():
    """Sign in, restart, still signed in, sign out — the path a user actually walks."""
    from music_agent.backends import cadence
    from music_agent import config

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
            with mock.patch("music_agent.net.httpmin.Session.request", transport):
                client = cadence.client_from_config(cfg)
                assert client.base_url == "https://cadence.example", client.base_url
                assert client.login("bob", "hunter2")["username"] == "bob"

                # The session has to reach DISK, or the next launch asks for the password again —
                # and it has to reach it SEALED. Comparing the raw file to the plaintext cookie could
                # only ever pass on a machine where DPAPI was unavailable.
                assert config.load_config()["cadence_session"] == "server-cookie"
                on_disk = json.load(open(config.config_path()))["cadence_session"]
                assert on_disk and (os.name != "nt" or on_disk.startswith(config.ENC_PREFIX)), on_disk
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


def test_cadence_login_refused():
    """A wrong password must not leave anything behind."""
    from music_agent.backends import cadence
    from music_agent import config

    real_app_dir = config.app_dir
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            cfg = config.load_config()
            cfg["cadence_url"] = "https://cadence.example"
            with mock.patch("music_agent.net.httpmin.Session.request", lambda *a, **k: reply(401, None, b"")):
                try:
                    cadence.client_from_config(cfg).login("bob", "wrong")
                    raise AssertionError("a 401 must raise, not return")
                except cadence.CadenceError:
                    pass
            assert config.load_config()["cadence_session"] == ""
    finally:
        config.app_dir = real_app_dir


def test_credential_write_does_not_clobber():
    """A long-lived client persisting a credential must not revert what Settings saved meanwhile.

    Both backends hold the config dict they were constructed with and write it back much later — the
    Cadence cookie slides on every command, the Spotify refresh token rotates. Writing that captured
    dict wholesale silently undid any hotkey or mode saved in between. This is the regression test.
    """
    from music_agent.backends import cadence
    from music_agent import config
    from music_agent.backends import spotify

    real_app_dir = config.app_dir
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            cfg = config.load_config()
            cfg["cadence_url"] = "https://cadence.example"
            config.save_config(cfg)

            client = cadence.client_from_config(cfg)          # captures cfg as it is now
            spotify = spotify.controller_from_config(
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


def test_credentials_encrypted_at_rest():
    """Every credential really is sealed on the way out — including the ones added since.

    DPAPI is a no-op off Windows (`_dpapi` returns None), so nothing else here exercises the encrypt/
    decrypt path at all. This stands in a reversible fake for it, which is enough to prove the wiring:
    that SECRET_FIELDS covers every credential in DEFAULTS, that save_config seals all of them, that
    load_config unseals them, and that mode/hotkeys deliberately stay readable.
    """
    from music_agent import config

    # Any field holding a credential must be in SECRET_FIELDS. Named explicitly rather than pattern-
    # matched, so adding one to DEFAULTS and forgetting to seal it fails right here.
    credentials = {"cadence_url", "cadence_session", "cadence_username", "cadence_password",
                   "cf_access_client_id", "cf_access_client_secret",
                   "spotify_client_id", "spotify_client_secret", "spotify_refresh_token",
                   "proxy_user", "proxy_password"}
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
                            "spotify_client_secret": "CLIENTSECRET-VALUE-C",
                            "cadence_username": "ACCOUNT-VALUE-D",
                            "cadence_password": "PASSWORD-VALUE-E",
                            "proxy_user": "PROXYUSER-VALUE-F",
                            "proxy_password": "PROXYPASS-VALUE-G"})
                cfg["hotkeys"]["play_pause"] = "f8"
                config.save_config(cfg)

                raw = json.load(open(config.config_path()))
                for key in config.SECRET_FIELDS:
                    if raw[key]:
                        assert raw[key].startswith(config.ENC_PREFIX), f"{key} written in the clear"
                for secret in ("SESSION-VALUE-A", "REFRESHTOKEN-VALUE-B", "CLIENTSECRET-VALUE-C",
                               "ACCOUNT-VALUE-D", "PASSWORD-VALUE-E", "PROXYUSER-VALUE-F",
                               "PROXYPASS-VALUE-G"):
                    assert secret not in open(config.config_path()).read(), f"{secret!r} is on disk"
                assert raw["mode"] == "spotify" and raw["hotkeys"]["play_pause"] == "f8"

                back = config.load_config()
                assert back["cadence_session"] == "SESSION-VALUE-A"
                # The window shows these back to you, so a one-way trip would look like "it forgot".
                assert back["cadence_username"] == "ACCOUNT-VALUE-D"
                assert back["cadence_password"] == "PASSWORD-VALUE-E"
                assert back["proxy_user"] == "PROXYUSER-VALUE-F"
                assert back["proxy_password"] == "PROXYPASS-VALUE-G"
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


def test_corrupt_config_never_raises():
    """load_config's contract: any garbage on disk yields defaults rather than stopping the launch."""
    from music_agent import config

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
    from music_agent.backends import spotify

    saved = []
    auth = spotify._Auth("cid", "csecret", f"http://127.0.0.1:{port}/callback", "", saved.append)
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
         mock.patch("music_agent.net.httpmin.post", return_value=reply(200, token)) as post:
        if wrong_state:
            try:
                auth.token()
                raise AssertionError("a mismatched state must be refused")
            except spotify.SpotifyError as e:
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




def test_spotify_consent_on_a_real_socket():
    spotify_consent(8899)


def test_spotify_consent_refuses_a_mismatched_state():
    spotify_consent(8902, wrong_state=True)


def test_spotify_consent_port_is_free_for_a_second_attempt():
    """The callback server must release its port, or the next consent cannot bind it."""
    spotify_consent(8899)


# ------------------------------------------------------- 4. a configured proxy carries the request
def test_a_proxy_from_the_config_file_actually_carries_the_request():
    """The proxy boxes in Settings have to reach `urllib`, not just the config file.

    Everything else about the proxy is checked by asserting on `os.environ`, which proves only that
    the app set a variable. This runs a REAL proxy on loopback and asks for a host that does not
    exist (`.invalid` is reserved and never resolves): if the request arrives at all, it arrived
    through the proxy, because nothing else could have found that address.

    It also pins the credential round trip. `config.proxy_url()` percent-encodes the account into the
    URL — a password containing `@ : /` would otherwise split it somewhere other than where it means
    to — and urllib unquotes it again into the Proxy-Authorization header. This is the only place the
    two halves of that meet.
    """
    import base64
    import http.server

    from music_agent import config
    from music_agent.net import httpmin

    seen = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            # Absolute-form request-target: the shape a client uses ONLY when talking to a proxy.
            seen.append((self.path, self.headers.get("Proxy-Authorization")))
            body = b'{"through":"proxy"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    real_app_dir = config.app_dir
    saved_env = {key: os.environ.get(key) for key in config.ENV_PROXY_KEYS}
    password = "p@ss:w/rd"          # every character that would break an unquoted URL
    user = "corp" + chr(92) + "me"  # a domain account, backslash and all
    try:
        with tempfile.TemporaryDirectory() as d:
            config.app_dir = lambda: d
            config.use_env(False)                     # the GUI's path: config file only
            cfg = config.load_config()
            cfg.update({"proxy_url": f"127.0.0.1:{port}", "proxy_user": user,
                        "proxy_password": password})
            config.save_config(cfg)

            # Nothing else is called: load_config applies the proxy itself, which is the promise --
            # every entry point gets it without knowing the proxy exists.
            config.load_config()
            assert os.environ.get("HTTPS_PROXY", "").startswith("http://corp%5Cme:"), os.environ.get("HTTPS_PROXY")
            assert password not in os.environ["HTTPS_PROXY"], "the password must be percent-encoded"

            answer = httpmin.request("GET", "http://music-agent.invalid/api/whatever", timeout=10)
            assert answer.json() == {"through": "proxy"}, answer.content
    finally:
        config.app_dir = real_app_dir
        for key, value in saved_env.items():
            os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
        server.shutdown()
        server.server_close()

    assert len(seen) == 1, seen
    target, authorization = seen[0]
    assert target == "http://music-agent.invalid/api/whatever", target
    assert authorization and authorization.startswith("Basic "), "the proxy account was not sent"
    decoded = base64.b64decode(authorization.split()[1]).decode()
    assert decoded == f"{user}:{password}", decoded
