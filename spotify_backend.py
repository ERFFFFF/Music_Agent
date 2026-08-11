"""Spotify mode: drive this machine's Spotify Connect device through the Web API.

A dozen REST calls and one OAuth exchange, written out by hand instead of pulling in `spotipy` —
which listed **redis** as a hard dependency and got bundled into the exe for it. The HTTP underneath is
httpmin, this project's stdlib `urllib` shim, so this file costs the project no package at all.

Same shape as cadence.py on purpose: a client whose `_api` turns every failure into one CadenceError-
style exception carrying the sentence to show the user, and a Controller exposing the same five actions
main.py and music_agent_cli.py bind hotkeys to. Errors come back as a *message*, never an exception,
because these run on the hotkey thread where an uncaught exception is a silently dead key.

The refresh token lives in cadence_config.txt with everything else (config.DEFAULTS); the access token
is 1h and stays in memory.
"""

import base64
import json
import logging
import os
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpmin

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
TIMEOUT = 15
CONSENT_TIMEOUT = 180   # how long the browser consent window may take before we give up waiting
CONSENT_RETRY_AFTER = 60  # ...and how long to refuse to reopen it after one fails

SCOPE = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-library-modify "
    "user-library-read"
)


class SpotifyConfigError(Exception):
    """No usable credentials. The app should say so and offer Settings, not half-run."""


class SpotifyError(Exception):
    """Anything the user needs to see: no device, not Premium, rate-limited, token dead."""


class _GrantRejected(SpotifyError):
    """The refresh token is dead. Internal: `token()` recovers from it by re-running consent."""


def _error_message(response):
    """Spotify's own words for a failure: `{"error": {"status": .., "message": ".."}}`, or "".

    Never raises — an error body that isn't the documented shape (or isn't JSON at all) has to fall
    back to the generic sentence, not replace one failure with a different one.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(error or "") if isinstance(error, str) else ""


# ------------------------------------------------------------------------------------ authorisation
class _Auth:
    """The Authorization Code flow, by hand.

    Spotify hands back a refresh token once, at first consent; from then on this swaps it for an access
    token whenever the old one is close to expiry. `on_token` persists the refresh token — Spotify may
    return a NEW one on any refresh, and dropping it silently would strand the user at the next launch.
    """

    def __init__(self, client_id, client_secret, redirect_uri, refresh_token="", on_token=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.refresh_token = refresh_token
        self.on_token = on_token
        self._access = ""
        self._expires_at = 0.0
        # Consent blocks the calling thread — which is the hotkey thread — so it gets a lock (two keys
        # pressed together must not open two browser tabs) and a cooldown (after a failed or abandoned
        # consent, every further press would otherwise queue another 3-minute wait).
        self._consent_lock = threading.Lock()
        self._consent_blocked_until = 0.0

    def _basic(self):
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _post(self, data):
        try:
            r = httpmin.post(f"{ACCOUNTS}/api/token", data=data,
                             headers={"Authorization": self._basic(),
                                      "Content-Type": "application/x-www-form-urlencoded"},
                             timeout=TIMEOUT)
        except httpmin.RequestError as e:
            raise SpotifyError(f"Can't reach Spotify: {e}") from e
        payload = r.json() if r.content else {}
        if payload.get("error") == "invalid_grant":
            raise _GrantRejected(payload.get("error_description") or "The Spotify sign-in has expired.")
        if r.status_code >= 400:
            # No "…in Settings": the CLI has none, and both front ends now read these from a .env.
            # Name the SETTING, and let the caller say where its copy of it lives.
            raise SpotifyError(f"Spotify rejected the sign-in ({payload.get('error', r.status_code)}). "
                               "Check the Spotify Client ID and Client Secret.")
        self._access = payload["access_token"]
        # 60s of slack: a token that expires mid-request would surface as a mystery 401.
        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60
        if payload.get("refresh_token"):
            self.refresh_token = payload["refresh_token"]
            if self.on_token:
                self.on_token(self.refresh_token)
        return self._access

    def token(self, force=False):
        if not force and self._access and time.time() < self._expires_at:
            return self._access
        if self.refresh_token:
            try:
                return self._post({"grant_type": "refresh_token",
                                   "refresh_token": self.refresh_token})
            except _GrantRejected as e:
                # Spotify's refresh tokens last 6 months, and a password change or the user removing
                # the app kills one sooner. That is a SCHEDULED event, not a fault — so drop the dead
                # token (and the copy in the config) and fall through to consent. Without this the app
                # would fail identically forever with no route back except editing the config by hand.
                logging.info("Spotify refresh token is no longer valid (%s) — re-authorising.", e)
                self.refresh_token = ""
                if self.on_token:
                    self.on_token("")
        return self._authorize()

    def _authorize(self):
        """First run only: open the browser, catch the redirect on the loopback address, swap the code.

        The redirect URI has to be a loopback IP literal (http://127.0.0.1:PORT/path) and has to match
        the one registered on the Spotify app character for character — that is why config keeps it as
        an editable field rather than hard-coding a port.
        """
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if parsed.hostname not in ("127.0.0.1", "::1") or not parsed.port:
            raise SpotifyConfigError(
                f"The redirect URI must be a loopback address with a port, e.g. "
                f"http://127.0.0.1:8888/callback — got {self.redirect_uri!r}.")

        if time.time() < self._consent_blocked_until:
            raise SpotifyError("Finish the Spotify sign-in in your browser, then press the key again.")
        with self._consent_lock:
            if time.time() < self._consent_blocked_until:
                raise SpotifyError("Finish the Spotify sign-in in your browser, then press the key again.")
            # Block further attempts for as long as this one can run, plus a cooldown. Cleared on
            # success; left in place if _consent raises, which IS the cooldown.
            self._consent_blocked_until = time.time() + CONSENT_TIMEOUT + CONSENT_RETRY_AFTER
            token = self._consent(parsed)
            self._consent_blocked_until = 0.0
            return token

    def _consent(self, parsed):
        state = secrets.token_urlsafe(16)
        query = urllib.parse.urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPE,
            "state": state,
        })
        result = {}

        class Handler(BaseHTTPRequestHandler):
            # Bounds READING a request, which HTTPServer.timeout does not: that one only bounds waiting
            # for a connection. Without this, any socket that connects and then sends nothing — a
            # browser pre-connect, a security product probing the new listener, a port scan — parks
            # handle_one_request() in rfile.readline() forever, and because this runs on the keyboard
            # thread every hotkey stays dead until the app is killed. Measured before the fix.
            timeout = 10

            def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's name
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" in params or "error" in params:
                    result.update({k: v[0] for k, v in params.items()})
                    body = b"Music Agent is authorised. You can close this tab."
                else:
                    body = b"Waiting for Spotify..."   # the browser's favicon probe lands here
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass   # don't print HTTP noise into a tray app's log

        logging.info("Opening the browser for Spotify authorisation.")

        class Server(HTTPServer):
            # Bind the host the redirect URI actually names. Hardcoding 127.0.0.1 meant an app
            # registered with the IPv6 loopback (which the validator above accepts, and Spotify's docs
            # list as valid) passed validation and could then never receive its redirect.
            address_family = socket.AF_INET6 if parsed.hostname == "::1" else socket.AF_INET

        with Server((parsed.hostname, parsed.port), Handler) as server:
            server.timeout = CONSENT_TIMEOUT
            webbrowser.open(f"{ACCOUNTS}/authorize?{query}")
            deadline = time.time() + CONSENT_TIMEOUT
            while not result and time.time() < deadline:
                server.handle_request()   # returns on timeout too, so the loop can't hang forever

        if result.get("error"):
            raise SpotifyError(f"Spotify authorisation was refused ({result['error']}).")
        if not result.get("code"):
            raise SpotifyError("Timed out waiting for Spotify authorisation.")
        if result.get("state") != state:
            # Someone else's redirect reached our port. Refusing is the only safe move.
            raise SpotifyError("Spotify authorisation failed a security check — try again.")
        return self._post({"grant_type": "authorization_code", "code": result["code"],
                           "redirect_uri": self.redirect_uri})


# ------------------------------------------------------------------------------------ the controller
class SpotifyController:
    """The five hotkey actions, spoken in Spotify. Same contract as cadence.CadenceController: every
    method returns the notification text to show, or None for "say nothing"."""

    def __init__(self, cfg, on_token=None):
        if not (cfg.get("spotify_client_id") and cfg.get("spotify_client_secret")):
            raise SpotifyConfigError(
                "Spotify mode needs a Client ID and Client Secret, or switch to Cadence mode if you "
                "don't have a Spotify app.")
        # See CadenceController.last_error: every action returns text rather than raising, which a
        # hotkey wants and a script cannot read. This is how the CLI picks its exit code.
        self.last_error = None
        self.auth = _Auth(cfg["spotify_client_id"], cfg["spotify_client_secret"],
                          cfg.get("spotify_redirect_uri") or "http://127.0.0.1:8888/callback",
                          cfg.get("spotify_refresh_token", ""), on_token)
        self.device_id = None

    # ---------------------------------------------------------------- plumbing
    def _api(self, method, path, **kw):
        """One request, with every failure turned into a sentence worth showing."""
        for attempt in (1, 2):
            try:
                r = httpmin.request(method, API + path, timeout=TIMEOUT,
                                    headers={"Authorization": f"Bearer {self.auth.token(force=attempt == 2)}"},
                                    **kw)
            except httpmin.RequestError as e:
                raise SpotifyError(f"Can't reach Spotify: {e}") from e
            if r.status_code != 401 or attempt == 2:
                break
            # A token can die before its stated expiry (password change, scope revoked). One forced
            # refresh, then believe it.

        if r.status_code == 401:
            raise SpotifyError("Spotify sign-in expired — sign in again: `login`, or Settings.")
        if r.status_code == 403:
            # NOT always Premium, and saying so to a Premium subscriber is unactionable nonsense.
            # Measured against a live account: skipping on a device whose context has no next track
            # answers 403 `Player command failed: Restriction violated`. Same status, different fix —
            # so relay what Spotify said and keep the Premium sentence for when it means it.
            detail = _error_message(r)
            if detail and "premium" not in detail.lower():
                raise SpotifyError(f"Spotify refused that: {detail}.")
            raise SpotifyError("Spotify refused the request — controlling playback needs Premium.")
        if r.status_code == 404:
            self.device_id = None      # whatever we had is gone; rediscover on the next press
            raise SpotifyError("No active Spotify device — start Spotify somewhere first.")
        if r.status_code == 429:
            raise SpotifyError(f"Spotify is rate-limiting — try again in "
                               f"{r.headers.get('Retry-After', 'a few')}s.")
        if r.status_code >= 400:
            raise SpotifyError(f"Spotify error {r.status_code}.")
        # A SUCCESS FROM THIS API IS NOT RELIABLY JSON, and the status code does not tell you.
        # Measured, all of them successes: play/pause/next/previous answer 204 empty; GET /me/player
        # answers 204 empty when nothing is playing; /me/library save+remove answer 200 with an empty
        # body; and **PUT /me/player/play and /pause answer 200 with an opaque 27-character token and
        # no Content-Type header at all** (undocumented — `A3_PtwniwTUebcLjlJgM73TGjkU`). Testing the
        # status alone crashes on the third case, testing the body alone crashes on the fourth: the
        # command had worked, and the app reported "Spotify sent something unexpected".
        #
        # So: anything that doesn't parse is not data. Every caller either ignores a write's result or
        # reads a documented shape out of a GET, and a real failure was a status above, not this.
        if r.status_code == 204 or not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    def devices(self):
        """Every Spotify Connect device this account can see. Public because `... devices` is the
        'why won't it play' command, and it used to be a whole 48-line script with its own dependency."""
        return self._api("GET", "/me/player/devices").get("devices", [])

    def _device(self):
        """The device to command, discovered on demand. No background rescan thread: the next hotkey
        press re-runs this anyway, so the thread only ever reached the same answer earlier."""
        if self.device_id is not None:
            return self.device_id
        # `id` is documented nullable and a restricted device accepts no Web API commands at all —
        # picking either one is a permanent no-op while a working device sits further down the list.
        usable = [d for d in self.devices() if d.get("id") and not d.get("is_restricted")]
        chosen = next((d for d in usable if d.get("is_active")), None) or (usable[0] if usable else None)
        if not chosen:
            return None
        self.device_id = chosen["id"]
        logging.info("Using Spotify device %r (id=%s)", chosen.get("name"), self.device_id)
        if not chosen.get("is_active"):
            # Make it the active device first. This is what the old build's startup transfer_playback
            # did, and dropping it broke skip outright: next/previous 404 against an idle device, and
            # the 404 handler then advises "start Spotify" at someone whose Spotify is already running.
            # play_pause happened to work because PUT /me/player/play activates as a side effect.
            self._api("PUT", "/me/player", json={"device_ids": [self.device_id], "play": False})
        return self.device_id

    def _act(self, call):
        """Run one action and return TEXT, never raise. Mirrors CadenceController's `_send`, so main.py
        and music_agent_cli.py can treat the two backends identically.

        The bare `except Exception` is deliberate and is the whole point of this method: these run on
        the keyboard thread, where anything that escapes kills that hotkey silently and permanently.
        A malformed response is the realistic case — `toggle_like` indexes into what Spotify sends, so
        an unexpected shape lands here as a KeyError rather than a SpotifyError.
        """
        self.last_error = None
        try:
            return call()
        except (SpotifyError, SpotifyConfigError) as e:
            logging.error("Spotify action failed: %s", e)
            return self._fail(str(e))
        except Exception as e:  # noqa: BLE001 — see above
            logging.error("Spotify action crashed: %s", e, exc_info=True)
            return self._fail("Spotify sent something unexpected — see the log.")

    def _fail(self, message):
        self.last_error = message
        return message

    # ---------------------------------------------------------------- the five actions
    def play_pause(self):
        return self._act(lambda: self._transport(None))

    def pause(self):
        return self._act(lambda: self._transport(False))

    def resume(self):
        return self._act(lambda: self._transport(True))

    def _transport(self, want_playing):
        """Reach a play state; `None` means toggle (what the hotkey binds).

        Reading before writing is unavoidable for a toggle, and it is what makes `pause` and `resume`
        idempotent as well: Spotify answers **403 Restriction violated** when you pause an already-
        paused player, and _api turns any 403 into "controlling playback needs Premium" — an alarming
        and completely wrong sentence to show someone who pressed pause twice.

        Spotify documents that ordering between Player calls is not guaranteed, so there is
        deliberately no confirming read AFTER the write: mashing the key would report the wrong state.
        """
        state = self._api("GET", "/me/player")
        device = (state.get("device") or {}).get("id") or self._device()
        if not device:
            return self._fail("No Spotify device found — start Spotify somewhere first.")
        playing = bool(state.get("is_playing"))
        target = (not playing) if want_playing is None else want_playing
        if target == playing:
            return None
        self._api("PUT", "/me/player/play" if target else "/me/player/pause",
                  params={"device_id": device})
        logging.info("Started playback." if target else "Paused playback.")
        return None

    def next_track(self):
        return self._act(lambda: self._skip("next", "Skipped to next track."))

    def previous_track(self):
        return self._act(lambda: self._skip("previous", "Skipped to previous track."))

    def _skip(self, direction, logged):
        device = self._device()
        if not device:
            return self._fail("No Spotify device found — start Spotify somewhere first.")
        self._api("POST", f"/me/player/{direction}", params={"device_id": device})
        logging.info(logged)
        return None

    def toggle_like(self):
        def go():
            playback = self._api("GET", "/me/player")
            track = (playback.get("item") or {}).get("id")
            if not track:
                return "Nothing playing right now."
            # /me/library, not the deprecated /me/tracks — and it takes full `spotify:track:` URIs in
            # a query param, not bare ids. urlencode percent-encodes the colons, which is what the
            # docs show. Save/remove answer 200 with an EMPTY body (not 204); _api's guard covers both.
            uri = f"spotify:track:{track}"
            liked = self._api("GET", "/me/library/contains", params={"uris": uri})[0]
            self._api("DELETE" if liked else "PUT", "/me/library", params={"uris": uri})
            logging.info("%s track %s.", "Unliked" if liked else "Liked", track)
            return "Unliked" if liked else "Liked"
        return self._act(go)

    def show_current(self):
        def go():
            playback = self._api("GET", "/me/player")
            item = playback.get("item")
            if not item:
                return "Nothing playing right now."
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            logging.info("Displayed current song: %s by %s", item["name"], artists)
            # The paused marker matters more here than it looks: `pause` and `resume` print nothing on
            # success, so `now` is the only way to see which state you are in — and CadenceController
            # already reports it, so without this the same command answered differently per mode.
            return f"{item['name']} — {artists}" + ("" if playback.get("is_playing") else "  (paused)")
        return self._act(go)


def _legacy_refresh_token():
    """The token spotipy left in AppData/.cache, read once so an upgrade doesn't send an already-
    authorised install back through browser consent for a credential it had on disk all along.

    Not part of config._migrate: that only runs when cadence_config.txt is ABSENT, and every install
    since the one-config-file change already has one."""
    from config import appdata_dir

    try:
        with open(os.path.join(appdata_dir(), ".cache"), encoding="utf-8") as f:
            return json.load(f).get("refresh_token") or ""
    except (OSError, ValueError, AttributeError):
        return ""


def controller_from_config(cfg):
    """A controller wired to the saved settings, persisting any refreshed token back into the same
    config file. The one construction path — main.py and music_agent_cli.py both use it."""
    from config import update_config

    if not cfg.get("spotify_refresh_token"):
        inherited = _legacy_refresh_token()
        if inherited:
            logging.info("Adopted the Spotify refresh token from the old spotipy cache.")
            update_config("spotify_refresh_token", inherited, cfg)

    def remember(refresh_token):
        # update_config, not save_config(cfg) — see cadence.client_from_config for why.
        update_config("spotify_refresh_token", refresh_token, cfg)

    return SpotifyController(cfg, on_token=remember)


def selftest():
    """Offline checks for the parts that fail silently: the empty-body rule (a 204 must never reach
    .json()), the status-to-sentence mapping, and that a bad `state` is refused."""
    from types import SimpleNamespace
    from unittest import mock

    cfg = {"spotify_client_id": "id", "spotify_client_secret": "secret",
           "spotify_redirect_uri": "http://127.0.0.1:8888/callback", "spotify_refresh_token": "rt"}

    try:
        SpotifyController({"spotify_client_id": "", "spotify_client_secret": ""})
        raise AssertionError("missing credentials must be refused up front")
    except SpotifyConfigError:
        pass

    def response(status, body=b"", json_value=None, headers=None):
        return SimpleNamespace(status_code=status, content=body, headers=headers or {},
                               json=lambda: json_value)

    def _Real(status, body=b"", headers=None):
        """A REAL httpmin.Response, so .json() parses — or raises — exactly as it does in production.
        The stand-in above hands back a pre-decided value, which is fine for shape checks and useless
        for the cases below, where the whole question is what a malformed body does."""
        import email.message
        message = email.message.Message()
        for key, value in (headers or {}).items():
            message[key] = value
        return httpmin.Response(status, body, message, API + "/x")

    c = SpotifyController(cfg)
    c.auth._access, c.auth._expires_at = "tok", time.time() + 999

    # 204 with an empty body is the success case for every playback command — .json() would raise
    with mock.patch("httpmin.request", return_value=response(204)):
        assert c._api("PUT", "/me/player/pause") == {}
    # ...and so is 200 with an empty body, which is what /me/library save+remove actually answer.
    # Testing the status alone instead of the body is the easiest way to crash this file.
    with mock.patch("httpmin.request",
                    return_value=response(200, b"", json_value=AssertionError)) as m:
        assert c._api("PUT", "/me/library", params={"uris": "spotify:track:x"}) == {}
        assert m.call_args.kwargs["params"] == {"uris": "spotify:track:x"}, "library takes URIs, not ids"
    # ...and a 200 with a body still parses
    with mock.patch("httpmin.request", return_value=response(200, b"{}", {"is_playing": True})):
        assert c._api("GET", "/me/player") == {"is_playing": True}

    # ...and neither does a 200 carrying something that ISN'T JSON. Measured: PUT /me/player/play and
    # /pause answer 200 with an opaque token and no Content-Type. The command SUCCEEDED; treating an
    # unparseable body as a crash reported "Spotify sent something unexpected" at a working pause.
    with mock.patch("httpmin.request", return_value=_Real(200, b"A3_PtwniwTUebcLjlJgM73TGjkU")):
        assert c._api("PUT", "/me/player/pause") == {}

    # A 403 is not always Premium. "Restriction violated" is what a skip gets when the current
    # context has nowhere to skip to — telling a Premium subscriber to buy Premium helps nobody.
    with mock.patch("httpmin.request", return_value=_Real(
            403, b'{"error":{"status":403,"message":"Player command failed: Restriction violated"}}')):
        try:
            c._api("POST", "/me/player/next")
            raise AssertionError("403 must raise")
        except SpotifyError as e:
            assert "Restriction violated" in str(e) and "Premium" not in str(e), str(e)
    # ...but a real Premium refusal still says so, and so does a 403 with no usable body
    for body in (b'{"error":{"status":403,"message":"Player command failed: Premium required"}}', b"", b"<html>"):
        with mock.patch("httpmin.request", return_value=_Real(403, body)):
            try:
                c._api("POST", "/me/player/next")
                raise AssertionError("403 must raise")
            except SpotifyError as e:
                assert "Premium" in str(e), (body, str(e))

    for status, expected, headers in ((403, "Premium", None), (404, "No active Spotify device", None),
                                      (429, "rate-limiting", {"Retry-After": "7"}),
                                      (500, "Spotify error 500", None)):
        with mock.patch("httpmin.request", return_value=response(status, headers=headers)):
            try:
                c._api("GET", "/me/player")
                raise AssertionError(f"{status} must raise")
            except SpotifyError as e:
                assert expected in str(e), (status, str(e))
    assert c.device_id is None, "a 404 must forget the cached device so the next press rediscovers"

    # an action never raises at the caller — it returns the sentence to show, and records that it
    # was a failure so a script can act on it (the text alone is indistinguishable from a track name)
    with mock.patch("httpmin.request", return_value=response(403)):
        assert "Premium" in c.show_current() and c.last_error
    with mock.patch("httpmin.request", return_value=response(
            200, b"{}", {"is_playing": True, "item": {"id": "T", "name": "Song", "artists": [{"name": "A"}]}})):
        assert c.show_current() == "Song — A" and c.last_error is None, "a success must clear it"
    # ...and `now` has to say WHICH state it is in — pause and resume print nothing on success, and
    # CadenceController.now_playing_text already marks it, so the two modes must agree.
    with mock.patch("httpmin.request", return_value=response(
            200, b"{}", {"is_playing": False, "item": {"id": "T", "name": "Song", "artists": []}})):
        assert c.show_current() == "Song —   (paused)", c.show_current()
    with mock.patch("httpmin.request", return_value=response(200, b"{}", {"devices": []})):
        c.device_id = None
        assert "No Spotify device" in c.next_track() and c.last_error

    # ...including when Spotify sends a shape we didn't expect. toggle_like indexes into the response,
    # so an empty body used to escape as KeyError and reach the user as a traceback (cli) or a dead
    # hotkey (tray). _act's bare except is what makes the "returns text, never raises" contract true.
    def malformed(_method, url, **_kw):
        if url.endswith("/me/player"):
            return response(200, b"{}", {"item": {"id": "T1", "name": "S", "artists": []}})
        return response(200, b"")            # contains answers 200 with nothing in it
    with mock.patch("httpmin.request", side_effect=malformed):
        assert isinstance(c.toggle_like(), str), "a malformed response must become a message"

    # pause/resume must not write when the player is already in the wanted state: Spotify answers a
    # redundant pause with 403, which _api reports as "needs Premium" — the most alarming possible
    # wrong answer to someone who pressed pause twice.
    def player(is_playing):
        def transport(method, url, **_kw):
            if url.endswith("/me/player"):
                return response(200, b"{}", {"is_playing": is_playing,
                                             "device": {"id": "DEV"}, "item": {"id": "T1"}})
            writes.append((method, url))
            return response(204)
        return transport

    for is_playing, call, expected in ((True, "pause", ["/me/player/pause"]),
                                       (True, "resume", []),
                                       (False, "resume", ["/me/player/play"]),
                                       (False, "pause", []),
                                       (True, "play_pause", ["/me/player/pause"]),
                                       (False, "play_pause", ["/me/player/play"])):
        writes = []
        with mock.patch("httpmin.request", side_effect=player(is_playing)):
            assert getattr(c, call)() is None, (call, is_playing)
        assert [url[len(API):] for _m, url in writes] == expected, (call, is_playing, writes)

    # a token still inside its window is reused rather than re-fetched
    with mock.patch.object(_Auth, "_post", side_effect=AssertionError("should not refresh")):
        assert c.auth.token() == "tok"

    # a refresh that returns a new refresh token must persist it
    saved = []
    auth = _Auth("id", "secret", "http://127.0.0.1:8888/callback", "old", saved.append)
    with mock.patch("httpmin.post", return_value=response(
            200, b"{}", {"access_token": "a", "expires_in": 3600, "refresh_token": "new"})):
        assert auth.token() == "a"
    assert saved == ["new"] and auth.refresh_token == "new"
    # ...and one that omits it must keep the old one rather than blanking it
    with mock.patch("httpmin.post", return_value=response(
            200, b"{}", {"access_token": "b", "expires_in": 3600})):
        assert auth.token(force=True) == "b"
    assert auth.refresh_token == "new" and saved == ["new"]

    # a refresh token Spotify has rejected (they expire at 6 months) must be dropped here AND
    # upstream, so the next call re-runs consent instead of failing identically forever
    cleared = []
    dead = _Auth("id", "secret", "http://127.0.0.1:8888/callback", "expired", cleared.append)
    with mock.patch("httpmin.post", return_value=response(
            400, b"{}", {"error": "invalid_grant", "error_description": "Refresh token revoked"})):
        with mock.patch.object(_Auth, "_authorize", return_value="fresh") as consent:
            assert dead.token() == "fresh"
    assert consent.called, "a rejected refresh must fall through to consent"
    assert dead.refresh_token == "" and cleared == [""], "the dead token must be cleared upstream too"

    # ...but a plain credentials error must NOT clear anything — retrying is the right move there
    kept = []
    auth3 = _Auth("id", "bad-secret", "http://127.0.0.1:8888/callback", "good", kept.append)
    with mock.patch("httpmin.post", return_value=response(400, b"{}", {"error": "invalid_client"})):
        try:
            auth3.token()
            raise AssertionError("a bad client secret must raise")
        except SpotifyError as e:
            assert "Client ID and Client Secret" in str(e), str(e)
    assert auth3.refresh_token == "good" and kept == []

    # a non-loopback redirect URI can never complete the flow — say so before opening a browser
    bad = _Auth("id", "secret", "https://example.com/callback")
    try:
        bad.token()
        raise AssertionError("a non-loopback redirect URI must be refused")
    except SpotifyConfigError:
        pass
    print("spotify self-check ok")


if __name__ == "__main__":
    selftest()
