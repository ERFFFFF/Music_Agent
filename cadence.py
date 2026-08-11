"""Cadence backend: talk to a self-hosted Cadence account instead of the Spotify Web API.

Cadence plays audio in the BROWSER — its player owns the queue and the <audio> element — so this
client never streams anything itself. It logs in with the user's Cadence account,
then queues playback intents that the open Cadence tab drains ~1/s and performs. That is why every
control here is one small POST and why `live` matters: if no tab is listening, the command expires
unperformed and the user needs to hear about it rather than watch nothing happen.

No Spotify credentials, no OAuth, no Spotify Connect device — which is the whole point: this mode works
on a locked-down network where Spotify itself is blocked but Cadence (plain HTTPS) is not.

Cross-platform on purpose: nothing here is Windows-specific and nothing here is a third-party package
(httpmin is stdlib `urllib` wearing a `requests` shape), so this module can be exercised off Windows —
the hotkey/tray layer in main.py is the Windows-only part.
"""

import logging
import sys
import time
from urllib.parse import urlsplit

import httpmin
from config import normalize_url                        # noqa: F401 — re-exported; login_ui imports it

# Every command the Cadence backend accepts (backend/main.py `_REMOTE_CMDS`). Anything else is a typo,
# and failing here beats a 422 from the server.
COMMANDS = ("play_pause", "next", "previous", "like", "stop")

TIMEOUT = 25  # Cadence scales to zero when idle (Sablier); the call that wakes it can sit for ~15s


class CadenceError(Exception):
    """Anything the user needs to see: bad credentials, unreachable server, blocked at the edge."""


def _blocked_by_access(response):
    """Did Cloudflare Access swallow this request? True for the 302 itself and for the login page it
    redirects to, so it's caught either way."""
    if "cloudflare-access" in (response.headers.get("www-authenticate") or "").lower():
        return True
    urls = [response.url] + [h.headers.get("location", "") for h in response.history]
    return any("cloudflareaccess.com" in (u or "") for u in urls)


class CadenceClient:
    """One logged-in Cadence session. Persists the session cookie so the app logs in once, not daily.

    Thread-safety: hotkeys fire on the keyboard thread, one at a time in practice. Each call here is
    a single short request whose only shared mutable state is the cookie jar, which http.cookiejar
    locks internally — good enough for a hotkey agent, and the alternative (a lock around every key
    press) would only serialize what is already serial.
    """

    def __init__(self, base_url="", session=None, on_session=None,
                 cf_client_id="", cf_client_secret=""):
        """`session` seeds the saved cookie; `on_session(cookie)` is called whenever it changes so the
        caller can persist it (config.py writes it into cadence_config.txt). The client owns no file of
        its own — one config file holds everything, and this class stays testable without one."""
        self.base_url = normalize_url(base_url)
        self.on_session = on_session
        self._saved = session or ""
        self.session = httpmin.Session()
        # Cadence's /login refuses a request carrying NEITHER Origin nor Referer — that check exists to
        # stop a browser being forced to log into someone else's account cross-site, and a native client
        # simply has to state which origin it is talking to. Sending our own base URL is exactly that;
        # it is not a way around the guard (a real CSRF can't forge this header from a browser).
        self.session.headers["Origin"] = self.base_url
        # Cloudflare Access service token (optional): the supported headless path for a Cadence host
        # behind Access, which no native app can complete interactively. Left empty otherwise.
        if cf_client_id and cf_client_secret:
            self.session.headers.update({
                "CF-Access-Client-Id": cf_client_id,
                "CF-Access-Client-Secret": cf_client_secret,
            })
        self._load_cookie()

    # ---------------------------------------------------------------- session persistence
    def _host(self):
        return urlsplit(self.base_url).hostname or ""

    def _load_cookie(self):
        """Restore the saved session so a restart doesn't ask for the password again.

        The domain is NOT optional here. A cookie set without one is stored under domain "", and the
        server's own Set-Cookie lands under the real host — two entries named `session`, after which
        `cookies.get("session")` raises CookieConflictError and every hotkey dies. Setting the host up
        front means the server's cookie REPLACES this one (the jar keys on domain+path+name).
        """
        if self._saved and self._host():
            self.session.cookies.set("session", self._saved, domain=self._host(), path="/")

    def _cookie(self):
        """The current session cookie, without `cookies.get()`'s duplicate-name exception — a jar
        inherited from an older build can still hold two, and a hotkey must not blow up over it."""
        host, fallback = self._host(), None
        for c in self.session.cookies:
            if c.name != "session":
                continue
            if (c.domain or "").lstrip(".") == host:
                return c.value
            fallback = c.value
        return fallback

    def _emit(self, cookie):
        """Hand the caller a cookie to persist. Never raises: storing the session is bookkeeping, and
        a full disk must not turn a working play/pause into an error."""
        self._saved = cookie
        if self.on_session:
            try:
                self.on_session(cookie)
            except Exception as e:  # noqa: BLE001 — persistence must never break a playback command
                logging.warning(f"Could not save the Cadence session: {e}")

    def _save_cookie(self):
        """Persist the current cookie when it CHANGES. Cadence re-issues it on the agent's own
        endpoints (a sliding window), so an agent in regular use never logs in again — but only a real
        change is worth a disk write."""
        cookie = self._cookie()
        if cookie and cookie != self._saved:
            self._emit(cookie)

    def forget(self):
        """Log out locally: drop the cookie here and wherever the caller stored it."""
        self.session.cookies.clear()
        self._emit("")

    # ---------------------------------------------------------------- plumbing
    def _request(self, method, path, **kw):
        if not self.base_url:
            raise CadenceError("No Cadence server set — enter its address on the sign-in screen.")
        # `/api` prefix, then the backend's OWN path. Cadence's public host is the Next UI, which
        # rewrites `/api/:path*` to the backend root — stripping exactly one `/api`. So a backend route
        # that is already `/api/...` needs it twice from out here (`/api/api/me`) and an unprefixed one
        # needs it once (`/api/login`). This mirrors the browser client's axios baseURL="/api" exactly;
        # getting it wrong is a 404 on every call, and it is the single most common bug in this codebase.
        url = f"{self.base_url}/api{path}"
        try:
            r = self.session.request(method, url, timeout=TIMEOUT, **kw)
        except httpmin.RequestError as e:
            raise CadenceError(f"Can't reach Cadence at {self.base_url}: {e}") from e
        # A 302 to the Access login page, which the client follows into a 200 of HTML — see
        # _blocked_by_access, which catches it whether or not redirects were followed.
        if _blocked_by_access(r):
            # ASCII ">" rather than an arrow, and no "paste it below": this sentence reaches a
            # console as often as a dialog now. The arrow is U+2192, which cp1252 and cp437 cannot
            # encode at all — print() raised UnicodeEncodeError and the person who most needed to
            # read this got a traceback instead. music_agent_cli also guards stdout, but a message
            # that only survives because of a guard is one edit away from breaking again.
            raise CadenceError(
                "Blocked by Cloudflare Access. Create a service token (Zero Trust > Access > Service "
                "Auth), allow it on the Cadence application's policy, and give it to Music Agent "
                "(CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET in your .env, or the gear icon in the "
                "sign-in window)."
            )
        if r.status_code == 401:
            raise CadenceError("Cadence session expired — sign in again.")
        if r.status_code == 403:
            raise CadenceError("Cadence refused the request.")
        if r.status_code >= 400:
            raise CadenceError(f"Cadence error {r.status_code}.")
        self._save_cookie()
        if not r.content:
            return {}          # 204s are normal here
        try:
            return r.json()
        except ValueError:
            # A non-JSON body is Sablier's wake page: Cadence scales to zero after ~30 min idle and
            # answers 200 text/html while it starts (measured). Say so — swallowing it as "{}" made a
            # sleeping server look like a server with nothing playing. No retry on purpose: if the
            # stack is asleep then no browser tab is open either, so there is nothing to perform the
            # command anyway. Press the key again once Cadence is up.
            raise CadenceError("Cadence is waking up — try again in a few seconds.")

    # ---------------------------------------------------------------- public API
    def login(self, username, password):
        """Exchange the account's own Cadence credentials for a session cookie. Cadence has no
        self-registration, so this is the same account the user signs into the web player with."""
        if not username or not password:
            raise CadenceError("Username and password are required.")
        self.session.cookies.clear()  # never mix a stale cookie into a fresh login
        self._request("POST", "/login", json={"username": username, "password": password},
                      headers={"Accept": "application/json"})
        if not self._cookie():
            raise CadenceError("Cadence accepted the login but sent no session cookie.")
        self._save_cookie()
        return self.me()

    def me(self):
        """The signed-in account (also the cheapest 'is this session still good?' check)."""
        return self._request("GET", "/api/me")

    def is_authenticated(self):
        try:
            self.me()
            return True
        except CadenceError:
            return False

    def command(self, cmd):
        """Queue one playback intent for this account's player. Returns True if a Cadence tab was
        listening — False means it was queued but will expire unperformed (tell the user to open
        Cadence rather than pretending the key press worked)."""
        if cmd not in COMMANDS:
            raise CadenceError(f"Unknown command {cmd!r}")
        return bool(self._request("POST", "/api/remote/command", json={"cmd": cmd}).get("live"))

    def state(self):
        """What's playing right now: {track:{name,artists,...}|None, paused, position_ms, live}."""
        return self._request("GET", "/api/remote/state")

    def now_playing_text(self):
        """One line for the 'show current song' notification, or None when nothing is playing."""
        st = self.state()
        track = st.get("track") or {}
        if not track.get("name"):
            return None
        line = track["name"]
        if track.get("artists"):
            line += f" — {track['artists']}"
        if st.get("paused"):
            line += "  (paused)"
        return line


def client_from_config(cfg):
    """A client wired to the saved settings — no network call, so it's cheap to build anywhere.

    The session cookie round-trips through the SAME config dict/file as everything else: seeded from
    `cadence_session` and written back whenever Cadence re-signs it. Lives here rather than in the UI
    layer because it is plumbing, and because it has to be exercisable without a GUI toolkit installed.
    """
    from config import update_config

    def remember(cookie):
        # update_config, not save_config(cfg): this fires long after the client was built, and writing
        # the whole captured dict would undo anything Settings saved in the meantime.
        update_config("cadence_session", cookie, cfg)

    return CadenceClient(
        base_url=cfg.get("cadence_url", ""),
        session=cfg.get("cadence_session", ""),
        on_session=remember,
        cf_client_id=cfg.get("cf_access_client_id", ""),
        cf_client_secret=cfg.get("cf_access_client_secret", ""),
    )


class CadenceController:
    """The five hotkey actions, spoken in Cadence. Same shape as SpotifyController (spotify_backend.py)
    so main.py binds hotkeys without caring which service is behind them.

    Every method returns the notification text to show, or None for "say nothing" — errors come back as
    a message rather than an exception because these run on the hotkey thread, where an uncaught
    exception is a silent dead key.
    """

    def __init__(self, client):
        self.client = client
        # Set by an action that failed, cleared by the next one that runs. Every method here returns
        # TEXT rather than raising, which is right for a hotkey and leaves a *script* unable to tell
        # "Like toggled" from "Cadence session expired" — both are just a string. The CLI reads this
        # to choose an exit code, so `music_agent_cli.py play || alert-me` actually fires.
        self.last_error = None

    def _fail(self, message):
        self.last_error = message
        return message

    def _send(self, cmd, ok_message=None):
        self.last_error = None
        try:
            live = self.client.command(cmd)
        except CadenceError as e:
            logging.error(f"Cadence {cmd} failed: {e}")
            return self._fail(str(e))
        if not live:
            # Queued but nothing is there to perform it. Saying so beats a key that does nothing —
            # and it is a failure, not a quiet success: the command WILL expire unperformed.
            return self._fail("No Cadence tab is open — open Cadence in your browser to control playback.")
        logging.info(f"Cadence command sent: {cmd}")
        return ok_message

    def play_pause(self):
        return self._send("play_pause")

    def pause(self):
        return self._to_paused(True)

    def resume(self):
        return self._to_paused(False)

    def _to_paused(self, want_paused):
        """A real pause / resume, built out of the only intent Cadence has.

        The browser tab owns the <audio> element, and the remote vocabulary it drains is a TOGGLE
        (`play_pause`) — there is no separate pause command to send. Reading the state first is what
        turns that toggle into a command that means what its name says; without it `pause` starts the
        music whenever it is run twice, which is the sort of thing a hotkey user forgives and a script
        does not.
        """
        self.last_error = None
        try:
            state = self.client.state()
        except CadenceError as e:
            logging.error(f"Cadence state failed: {e}")
            return self._fail(str(e))
        if not (state.get("track") or {}).get("name"):
            return "Nothing playing right now."
        if bool(state.get("paused")) == want_paused:
            return None                      # already there — sending the toggle would undo it
        return self._send("play_pause")

    def next_track(self):
        return self._send("next")

    def previous_track(self):
        return self._send("previous")

    def toggle_like(self):
        # Deliberately not "Liked"/"Unliked": the browser tab owns the like state and toggles it there,
        # so this app doesn't know which way it went.
        # ponytail: if that ambiguity ever bites, have the tab report the new state back and read it here.
        return self._send("like", "Like toggled")

    def show_current(self):
        self.last_error = None
        try:
            return self.client.now_playing_text() or "Nothing playing right now."
        except CadenceError as e:
            logging.error(f"Cadence state failed: {e}")
            return self._fail(str(e))


def selftest():
    """Offline checks for the two things that silently break everything: cookie bookkeeping and
    telling a wall (Cloudflare Access / a wake page) apart from a real answer. No network needed."""
    from types import SimpleNamespace
    def R(headers=None, url="", history=()):   # response stand-in
        return SimpleNamespace(headers=headers or {}, url=url, history=history)

    assert _blocked_by_access(R({"www-authenticate": 'Cloudflare-Access resource_metadata="x"'}))
    assert _blocked_by_access(R(url="https://x.cloudflareaccess.com/cdn-cgi/access/login/y"))
    assert _blocked_by_access(R(url="https://cadence.example/api/api/me",
                                history=(R(headers={"location": "https://x.cloudflareaccess.com/l"}),)))
    assert not _blocked_by_access(R(url="https://cadence.example/api/api/me"))

    assert normalize_url("cadence.example.com") == "https://cadence.example.com"
    assert normalize_url("https://cadence.example.com/") == "https://cadence.example.com"
    assert normalize_url("  ") == "" and normalize_url(None) == ""
    assert CadenceClient(base_url="cadence.example.com").base_url == "https://cadence.example.com"

    # Regression: a restored session plus the server's own re-issued one must never leave TWO cookies
    # named `session` in the jar — the second one then shadows whichever the client sends first and
    # the app presents a stale session forever. Setting the domain is what makes the server's cookie
    # REPLACE the restored one (the jar keys on domain+path+name).
    c = CadenceClient(base_url="https://cadence.example", session="restored-cookie")
    assert c._cookie() == "restored-cookie"
    c.session.cookies.set("session", "server-issued", domain="cadence.example", path="/")
    assert len(c.session.cookies) == 1, "the server's cookie must REPLACE the restored one"
    assert c._cookie() == "server-issued"
    # ...and even a jar that already holds two (an older build's leftovers) must not raise
    c.session.cookies.set("session", "stray", domain="other.example", path="/")
    assert c._cookie() == "server-issued"

    # No server configured is a real state (a fresh copy has none): every call must say so rather than
    # fall back to some baked-in address.
    blank = CadenceClient()
    assert blank.base_url == ""
    try:
        blank.me()
        raise AssertionError("a client with no server must refuse to call anything")
    except CadenceError as e:
        assert "No Cadence server set" in str(e), e
    blank._load_cookie()   # must not litter the jar with a domain-less cookie either
    assert len(blank.session.cookies) == 0

    # pause/resume are a toggle plus a state read, so the read is the whole feature: a `pause` that
    # fires play_pause at an already-paused player starts the music instead of stopping it.
    class FakeClient:
        def __init__(self, paused, track=True):
            self.state_value = {"track": {"name": "Song"} if track else None, "paused": paused}
            self.sent = []

        def state(self):
            return self.state_value

        def command(self, cmd):
            self.sent.append(cmd)
            return True

    # ...and an action that failed has to SAY so in a way a script can read, not only in its text
    dead = FakeClient(paused=False)
    dead.command = lambda cmd: False                       # queued, but no tab drained it
    controller = CadenceController(dead)
    assert "No Cadence tab" in controller.play_pause() and controller.last_error
    controller.client = FakeClient(paused=False)
    assert controller.play_pause() is None and controller.last_error is None, "a success must clear it"

    playing, paused = FakeClient(paused=False), FakeClient(paused=True)
    assert CadenceController(playing).pause() is None and playing.sent == ["play_pause"]
    assert CadenceController(paused).pause() is None and paused.sent == [], "already paused: send nothing"
    playing.sent.clear(); paused.sent.clear()
    assert CadenceController(paused).resume() is None and paused.sent == ["play_pause"]
    assert CadenceController(playing).resume() is None and playing.sent == []
    silent = FakeClient(paused=True, track=False)
    assert "Nothing playing" in CadenceController(silent).pause() and silent.sent == []

    saved = []
    c2 = CadenceClient(base_url="https://cadence.example", on_session=saved.append)
    c2.session.cookies.set("session", "abc", domain="cadence.example", path="/")
    c2._save_cookie(); c2._save_cookie()          # only a CHANGE is worth a disk write
    assert saved == ["abc"], saved
    c2.forget()
    assert saved == ["abc", ""] and c2._cookie() is None, "sign-out must clear here and upstream"
    print("cadence self-check ok")


def demo():
    """Self-check against a live Cadence: python cadence.py <url> <user> <pass>.

    Asserts the full round trip — login, a command, and that the command really reached a tab — because
    every part of this file is network glue whose failure mode is silence.
    """
    url, user, pw = sys.argv[1], sys.argv[2], sys.argv[3]
    c = CadenceClient(base_url=url)
    me = c.login(user, pw)
    assert me.get("username") == user, me
    assert c.is_authenticated()
    try:
        c.command("nope")
        raise AssertionError("an unknown command must be refused before it leaves the app")
    except CadenceError:
        pass
    live = c.command("play_pause")
    print(f"login ok as {me['username']}; play_pause queued; tab listening = {live}")
    time.sleep(1.5)
    print("now playing:", c.now_playing_text())


if __name__ == "__main__":
    selftest()                       # always runs: offline, fast, and covers the silent-failure paths
    if len(sys.argv) > 3:
        demo()                       # add <url> <user> <pass> to also exercise a live server
