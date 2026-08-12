"""Checks for `music_agent.net.winhttp` — moved here from the module's own selftest()."""

from music_agent.net.winhttp import *  # noqa: F401,F403 — the public surface under test
from music_agent.net.winhttp import (  # noqa: F401 — including the private names it exercises
    RequestError, Response, available, request)
import json as _json

import music_agent.net.winhttp as MODULE


def test_winhttp():
    """Everything that does not need a corporate proxy: the transport, headers, bodies, status
    mapping, redirect chain and cookie round trip, against a real loopback server."""
    import http.cookiejar
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from music_agent.net import httpmin

    if not available():
        print("winhttp self-check skipped (not Windows)")
        return

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, status, payload=b"", extra=()):
            self.send_response(status)
            for k, v in extra:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/redirect"):
                return self._reply(302, extra=[("Location", "/landed")])
            if self.path.startswith("/landed"):
                return self._reply(200, b'{"landed": true}')
            if self.path.startswith("/setcookie"):
                return self._reply(200, b"{}", [("Set-Cookie", "session=from-server; Path=/")])
            if self.path.startswith("/403"):
                return self._reply(403, b'{"error": "nope"}', [("Retry-After", "9")])
            if self.path.startswith("/empty"):
                return self._reply(200)
            return self._reply(200, _json.dumps({
                "path": self.path, "cookie": self.headers.get("Cookie", ""),
                "ua": self.headers.get("User-Agent", ""),
                "custom": self.headers.get("X-Custom", "")}).encode())

        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._reply(200, _json.dumps({"type": self.headers.get("Content-Type"),
                                          "body": raw.decode()}).encode())

        def log_message(self, *_a):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r = request("GET", base + "/echo", headers={"X-Custom": "yes"})
        assert r.status_code == 200 and r.json()["custom"] == "yes", r.content
        assert "MusicAgent" in r.json()["ua"]

        # a 4xx is DATA here too, or every "403 -> Premium" style message breaks
        r = request("GET", base + "/403")
        assert r.status_code == 403 and r.json()["error"] == "nope"
        assert r.headers.get("retry-after") == "9", "header lookup must stay case-insensitive"

        r = request("GET", base + "/empty")
        assert r.status_code == 200 and r.content == b""

        # the redirect chain has to survive, same as httpmin -- Cloudflare Access depends on it
        r = request("GET", base + "/redirect")
        assert r.json() == {"landed": True} and r.url.endswith("/landed")
        assert [h.status_code for h in r.history] == [302], r.history
        assert r.history[0].headers.get("location") == "/landed"

        assert request("GET", base + "/echo", params={"uris": "spotify:track:abc"}).json()["path"] \
            == "/echo?uris=spotify%3Atrack%3Aabc"
        assert request("POST", base + "/p", json={"a": 1}).json() == {
            "type": "application/json", "body": '{"a": 1}'}
        assert request("POST", base + "/p", data={"grant_type": "x"}).json() == {
            "type": "application/x-www-form-urlencoded", "body": "grant_type=x"}

        # cookies round-trip through the SHARED jar, because WinHTTP's own store is disabled
        jar = httpmin.CookieJar()
        jar.set("session", "restored", domain="127.0.0.1", path="/")
        assert request("GET", base + "/echo", cookies=jar).json()["cookie"] == "session=restored"
        request("GET", base + "/setcookie", cookies=jar)
        assert len(jar) == 1, [c.value for c in jar]
        assert request("GET", base + "/echo", cookies=jar).json()["cookie"] == "session=from-server"

        # ...and it produces the same Response type the rest of the app already handles
        assert isinstance(request("GET", base + "/echo"), Response)
        assert isinstance(jar, http.cookiejar.CookieJar)

        try:
            request("GET", "http://127.0.0.1:9/nothing", timeout=3)
            raise AssertionError("a dead port must raise")
        except RequestError:
            pass
    finally:
        server.shutdown()
