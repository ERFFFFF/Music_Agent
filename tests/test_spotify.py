"""Checks for `music_agent.backends.spotify` — moved here from the module's own selftest()."""

from music_agent.backends.spotify import *  # noqa: F401,F403 — the public surface under test
from music_agent.backends.spotify import (  # noqa: F401 — including the private names it exercises
    API, SpotifyConfigError, SpotifyController, SpotifyError, _Auth, httpmin, time)
import music_agent.backends.spotify as MODULE


def test_spotify():
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
    with mock.patch("music_agent.net.httpmin.request", return_value=response(204)):
        assert c._api("PUT", "/me/player/pause") == {}
    # ...and so is 200 with an empty body, which is what /me/library save+remove actually answer.
    # Testing the status alone instead of the body is the easiest way to crash this file.
    with mock.patch("music_agent.net.httpmin.request",
                    return_value=response(200, b"", json_value=AssertionError)) as m:
        assert c._api("PUT", "/me/library", params={"uris": "spotify:track:x"}) == {}
        assert m.call_args.kwargs["params"] == {"uris": "spotify:track:x"}, "library takes URIs, not ids"
    # ...and a 200 with a body still parses
    with mock.patch("music_agent.net.httpmin.request", return_value=response(200, b"{}", {"is_playing": True})):
        assert c._api("GET", "/me/player") == {"is_playing": True}

    # ...and neither does a 200 carrying something that ISN'T JSON. Measured: PUT /me/player/play and
    # /pause answer 200 with an opaque token and no Content-Type. The command SUCCEEDED; treating an
    # unparseable body as a crash reported "Spotify sent something unexpected" at a working pause.
    with mock.patch("music_agent.net.httpmin.request", return_value=_Real(200, b"A3_PtwniwTUebcLjlJgM73TGjkU")):
        assert c._api("PUT", "/me/player/pause") == {}

    # A 403 is not always Premium. "Restriction violated" is what a skip gets when the current
    # context has nowhere to skip to — telling a Premium subscriber to buy Premium helps nobody.
    with mock.patch("music_agent.net.httpmin.request", return_value=_Real(
            403, b'{"error":{"status":403,"message":"Player command failed: Restriction violated"}}')):
        try:
            c._api("POST", "/me/player/next")
            raise AssertionError("403 must raise")
        except SpotifyError as e:
            assert "Restriction violated" in str(e) and "Premium" not in str(e), str(e)
    # ...but a real Premium refusal still says so, and so does a 403 with no usable body
    for body in (b'{"error":{"status":403,"message":"Player command failed: Premium required"}}', b"", b"<html>"):
        with mock.patch("music_agent.net.httpmin.request", return_value=_Real(403, body)):
            try:
                c._api("POST", "/me/player/next")
                raise AssertionError("403 must raise")
            except SpotifyError as e:
                assert "Premium" in str(e), (body, str(e))

    for status, expected, headers in ((403, "Premium", None), (404, "No active Spotify device", None),
                                      (429, "rate-limiting", {"Retry-After": "7"}),
                                      (500, "Spotify error 500", None)):
        with mock.patch("music_agent.net.httpmin.request", return_value=response(status, headers=headers)):
            try:
                c._api("GET", "/me/player")
                raise AssertionError(f"{status} must raise")
            except SpotifyError as e:
                assert expected in str(e), (status, str(e))
    assert c.device_id is None, "a 404 must forget the cached device so the next press rediscovers"

    # an action never raises at the caller — it returns the sentence to show, and records that it
    # was a failure so a script can act on it (the text alone is indistinguishable from a track name)
    with mock.patch("music_agent.net.httpmin.request", return_value=response(403)):
        assert "Premium" in c.show_current() and c.last_error
    with mock.patch("music_agent.net.httpmin.request", return_value=response(
            200, b"{}", {"is_playing": True, "item": {"id": "T", "name": "Song", "artists": [{"name": "A"}]}})):
        assert c.show_current() == "Song — A" and c.last_error is None, "a success must clear it"
    # ...and `now` has to say WHICH state it is in — pause and resume print nothing on success, and
    # CadenceController.now_playing_text already marks it, so the two modes must agree.
    with mock.patch("music_agent.net.httpmin.request", return_value=response(
            200, b"{}", {"is_playing": False, "item": {"id": "T", "name": "Song", "artists": []}})):
        assert c.show_current() == "Song —   (paused)", c.show_current()
    with mock.patch("music_agent.net.httpmin.request", return_value=response(200, b"{}", {"devices": []})):
        c.device_id = None
        assert "No Spotify device" in c.next_track() and c.last_error

    # ...including when Spotify sends a shape we didn't expect. toggle_like indexes into the response,
    # so an empty body used to escape as KeyError and reach the user as a traceback (cli) or a dead
    # hotkey (tray). _act's bare except is what makes the "returns text, never raises" contract true.
    def malformed(_method, url, **_kw):
        if url.endswith("/me/player"):
            return response(200, b"{}", {"item": {"id": "T1", "name": "S", "artists": []}})
        return response(200, b"")            # contains answers 200 with nothing in it
    with mock.patch("music_agent.net.httpmin.request", side_effect=malformed):
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
        with mock.patch("music_agent.net.httpmin.request", side_effect=player(is_playing)):
            assert getattr(c, call)() is None, (call, is_playing)
        assert [url[len(API):] for _m, url in writes] == expected, (call, is_playing, writes)

    # a token still inside its window is reused rather than re-fetched
    with mock.patch.object(_Auth, "_post", side_effect=AssertionError("should not refresh")):
        assert c.auth.token() == "tok"

    # a refresh that returns a new refresh token must persist it
    saved = []
    auth = _Auth("id", "secret", "http://127.0.0.1:8888/callback", "old", saved.append)
    with mock.patch("music_agent.net.httpmin.post", return_value=response(
            200, b"{}", {"access_token": "a", "expires_in": 3600, "refresh_token": "new"})):
        assert auth.token() == "a"
    assert saved == ["new"] and auth.refresh_token == "new"
    # ...and one that omits it must keep the old one rather than blanking it
    with mock.patch("music_agent.net.httpmin.post", return_value=response(
            200, b"{}", {"access_token": "b", "expires_in": 3600})):
        assert auth.token(force=True) == "b"
    assert auth.refresh_token == "new" and saved == ["new"]

    # a refresh token Spotify has rejected (they expire at 6 months) must be dropped here AND
    # upstream, so the next call re-runs consent instead of failing identically forever
    cleared = []
    dead = _Auth("id", "secret", "http://127.0.0.1:8888/callback", "expired", cleared.append)
    with mock.patch("music_agent.net.httpmin.post", return_value=response(
            400, b"{}", {"error": "invalid_grant", "error_description": "Refresh token revoked"})):
        with mock.patch.object(_Auth, "_authorize", return_value="fresh") as consent:
            assert dead.token() == "fresh"
    assert consent.called, "a rejected refresh must fall through to consent"
    assert dead.refresh_token == "" and cleared == [""], "the dead token must be cleared upstream too"

    # ...but a plain credentials error must NOT clear anything — retrying is the right move there
    kept = []
    auth3 = _Auth("id", "bad-secret", "http://127.0.0.1:8888/callback", "good", kept.append)
    with mock.patch("music_agent.net.httpmin.post", return_value=response(400, b"{}", {"error": "invalid_client"})):
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
