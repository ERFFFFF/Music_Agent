"""Checks for `music_agent.net.httpmin` — moved here from the module's own selftest()."""

from music_agent.net.httpmin import *  # noqa: F401,F403 — the public surface under test
from music_agent.net.httpmin import (  # noqa: F401 — including the private names it exercises
    RequestError, Session, _json, post, request)
import music_agent.net.httpmin as MODULE


def test_httpmin():
    """The four behaviours the backends actually depend on, against a real loopback server: an error
    status arriving as data, an empty body staying empty, the redirect chain surviving, and a
    server-issued cookie replacing a restored one."""
    import socket
    import threading

    CRLF = bytes([13, 10])
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def _reply(self, status, body=b"", extra=()):
            self.send_response(status)
            for k, v in extra:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/redirect"):
                return self._reply(302, extra=[("Location", "/landed")])
            if self.path.startswith("/landed"):
                return self._reply(200, b'{"landed": true}')
            if self.path.startswith("/empty200"):
                return self._reply(200)
            if self.path.startswith("/403"):
                return self._reply(403, b'{"error": "nope"}', [("Retry-After", "7")])
            if self.path.startswith("/setcookie"):
                return self._reply(200, b"{}", [("Set-Cookie", "session=server-issued; Path=/")])
            if self.path.startswith("/echo"):
                return self._reply(200, _json.dumps({
                    "path": self.path, "cookie": self.headers.get("Cookie", ""),
                    "ua": self.headers.get("User-Agent", ""),
                    "custom": self.headers.get("X-Custom", "")}).encode())
            self._reply(404, b"")

        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._reply(200, _json.dumps({"type": self.headers.get("Content-Type"),
                                          "body": body.decode()}).encode())

        def log_message(self, *_a):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # A 4xx is data. Turning it into an exception is what would break every "403 -> Premium" style
        # message in spotify.
        r = request("GET", base + "/403")
        assert r.status_code == 403 and r.json()["error"] == "nope"
        assert r.headers.get("retry-after") == "7", "header lookup must be case-insensitive"

        # 200 with an empty body: a success everywhere in Spotify's library API, and .json() must be
        # the thing that raises, not the request.
        r = request("GET", base + "/empty200")
        assert r.status_code == 200 and r.content == b""
        try:
            r.json()
            raise AssertionError("an empty body is not JSON")
        except ValueError:
            pass

        # The redirect chain has to survive, or a Cloudflare Access block reads as a normal answer.
        r = request("GET", base + "/redirect")
        assert r.json() == {"landed": True} and r.url.endswith("/landed")
        assert [h.status_code for h in r.history] == [302]
        assert r.history[0].headers.get("location") == "/landed"
        assert request("GET", base + "/landed").history == [], "no redirect, no history"

        # params are percent-encoded the way Spotify's /me/library documents
        r = request("GET", base + "/echo", params={"uris": "spotify:track:abc"})
        assert r.json()["path"] == "/echo?uris=spotify%3Atrack%3Aabc", r.json()["path"]

        # bodies and headers
        assert post(base + "/p", json={"a": 1}).json() == {"type": "application/json", "body": '{"a": 1}'}
        assert post(base + "/p", data={"grant_type": "x"}).json() == {
            "type": "application/x-www-form-urlencoded", "body": "grant_type=x"}
        assert "MusicAgent" in request("GET", base + "/echo").json()["ua"]

        # Session: headers stick, and the server's cookie REPLACES a restored one rather than joining
        # it — two entries named `session` is the bug that once killed every hotkey on second launch.
        s = Session()
        s.headers["X-Custom"] = "yes"
        s.cookies.set("session", "restored", domain="127.0.0.1", path="/")
        assert s.request("GET", base + "/echo").json()["cookie"] == "session=restored"
        s.request("GET", base + "/setcookie")
        assert len(s.cookies) == 1, [c.value for c in s.cookies]
        answer = s.request("GET", base + "/echo").json()
        assert answer["cookie"] == "session=server-issued" and answer["custom"] == "yes"
        s.cookies.clear()
        assert len(s.cookies) == 0

        # nothing listening is a RequestError, not a Response
        try:
            request("GET", "http://127.0.0.1:9/nothing", timeout=2)
            raise AssertionError("a dead port must raise")
        except RequestError:
            pass

        # ...and so is a connection that dies MID-response. A proxy or load balancer that drops the
        # tail raises http.client.IncompleteRead, which is not an OSError and would otherwise sail
        # past every caller's error handling. Measured against a real truncating proxy.
        liar = socket.socket()
        liar.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        liar.bind(("127.0.0.1", 0))
        liar.listen(1)

        def truncate():
            conn, _ = liar.accept()
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK" + CRLF + b"Content-Length: 100" + CRLF * 2 + b"only-ten-b")
            conn.close()
        threading.Thread(target=truncate, daemon=True).start()
        try:
            request("GET", f"http://127.0.0.1:{liar.getsockname()[1]}/short", timeout=5)
            raise AssertionError("a truncated response must raise")
        except RequestError:
            pass
        finally:
            liar.close()
    finally:
        server.shutdown()
