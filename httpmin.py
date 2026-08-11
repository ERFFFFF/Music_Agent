"""Just enough of `requests` for this app, on the standard library alone.

Both backends make small JSON calls and nothing else — a dozen of them in total. `urllib.request` has
done that since Python 1.x, so `requests` was the last runtime dependency the CLI carried for work the
interpreter already ships. Removing it is the same trade the project already made for `spotipy`,
`appdirs`, `ratelimit` and `python-dotenv`: a named ceiling instead of a package.

    ponytail: this is a shim, not an HTTP library. It covers exactly what cadence.py and
    spotify_backend.py call — GET/POST/PUT/DELETE, JSON and form bodies, query params, a cookie jar,
    and the redirect chain. No streaming, no multipart, no connection pooling, no automatic retries.
    Reach for `requests` again if any of those ever become the shortest path.

What the two backends actually rely on, and therefore what is load-bearing here:

- **A non-2xx is a Response, not an exception.** `urllib` raises `HTTPError` for anything >= 400; both
  backends map status codes to sentences (403 -> "needs Premium", 404 -> "no device"), so that has to
  arrive as data. `HTTPError` is itself response-shaped, which is what makes the catch a two-liner.
- **`.content` before `.status_code`.** Spotify answers some successes with 200 and an EMPTY body, so
  `_api` tests the body; `.content` is always bytes, never None.
- **The redirect chain is kept.** Cloudflare Access answers a token-less request with a 302 to
  `*.cloudflareaccess.com`, which urllib follows into a 200 full of HTML — exactly as `requests` did.
  `cadence._blocked_by_access` reads `.history` to tell that wall apart from a real answer, so a
  redirect handler that silently forgets the hops turns a hard block into "nothing is playing".
- **Cookies key on (domain, path, name).** `CadenceClient` restores a saved session cookie and the
  server re-issues its own; if those land as two entries the jar has to collapse them, or the client
  sends a stale session forever. `http.cookiejar` has that rule built in — which is the reason this
  uses it rather than a dict.

Importable everywhere (pure stdlib), so cadence.py's self-check still runs off Windows.
"""

import http.client
import http.cookiejar
import json as _json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 30

# urllib's default is "Python-urllib/3.x", which edge providers do throttle or block outright. Naming
# the app is both more honest and less likely to be swept up by someone else's bot rule.
USER_AGENT = "MusicAgent/1.0 (+https://github.com/ERFFFFF/music_agent)"


class RequestError(Exception):
    """A request that never produced an HTTP answer at all: DNS, TLS, refused, timed out.

    The `requests.RequestException` stand-in. A 404 is NOT this — that is a Response with a status.
    """


class Response:
    """One HTTP answer. Same five attributes the backends read off a `requests` response."""

    __slots__ = ("status_code", "content", "headers", "url", "history")

    def __init__(self, status_code, content, headers, url, history=()):
        self.status_code = status_code
        self.content = content or b""
        # http.client.HTTPMessage — an email.message.Message, so .get() is case-insensitive, which is
        # what `www-authenticate` and `Retry-After` lookups here assume.
        self.headers = headers
        self.url = url
        self.history = list(history)

    def json(self):
        """Parse the body, or raise ValueError — including for an empty one, which both backends
        already treat as "no JSON here" (see cadence._request's wake-page branch)."""
        return _json.loads(self.content.decode("utf-8"))


class CookieJar(http.cookiejar.CookieJar):
    """A cookie jar with `requests`' `.set(name, value, domain=, path=)` on it.

    `set_cookie` replaces an entry with the same (domain, path, name) rather than appending, which is
    the property CadenceClient depends on: the server's own `session` cookie must REPLACE the restored
    one instead of sitting beside it.
    """

    def set(self, name, value, domain="", path="/"):
        self.set_cookie(http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain, domain_specified=bool(domain), domain_initial_dot=domain.startswith("."),
            path=path, path_specified=True, secure=False, expires=None, discard=True,
            comment=None, comment_url=None, rest={}, rfc2109=False))


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects like `requests` does, but remember them.

    One instance per request (see `request` below): a handler shared across calls would accumulate
    another call's hops, and `_blocked_by_access` would then report a Cloudflare wall long after the
    request that hit one.
    """

    def __init__(self):
        self.history = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.history.append(Response(code, b"", headers, req.full_url))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_transport = None


def use_windows_transport(enabled):
    """Send every request through winhttp.py instead of urllib, or stop doing so. Returns whether it
    is now in effect.

    The one thing urllib cannot do is authenticate to a proxy as the logged-in Windows user (it
    speaks Basic only, and a corporate proxy wants NTLM or Negotiate). Switching the whole transport
    is a bigger hammer than swapping one handler, but proxy auth happens during the CONNECT that
    establishes the tunnel — whoever opens the tunnel has to be the one that authenticates, so there
    is no smaller seam. One branch here covers both backends and both front ends.
    """
    global _transport
    _transport = None
    if not enabled:
        return False
    import winhttp            # local: winhttp imports FROM this module, so a top-level import cycles

    if not winhttp.available():
        return False
    _transport = winhttp.request
    logging.debug("HTTP transport: winhttp (Windows stack, proxy auth as the logged-in user)")
    return True


def request(method, url, *, params=None, json=None, data=None, headers=None,
            timeout=DEFAULT_TIMEOUT, cookies=None):
    """One request, one Response. `json=` sends a JSON body, `data=` a form-encoded one.

    `params` goes through `urlencode`, which percent-encodes the colons in `spotify:track:<id>` —
    the form Spotify's /me/library documents.
    """
    if _transport is not None:
        return _transport(method, url, params=params, json=json, data=data, headers=headers,
                          timeout=timeout, cookies=cookies)
    if params:
        joiner = "&" if urllib.parse.urlsplit(url).query else "?"
        url += joiner + urllib.parse.urlencode(params)

    body = None
    sent = {"User-Agent": USER_AGENT, **(headers or {})}
    if json is not None:
        body = _json.dumps(json).encode("utf-8")
        sent.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        sent.setdefault("Content-Type", "application/x-www-form-urlencoded")

    redirects = _Redirects()
    handlers = [redirects]
    if cookies is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))
    opener = urllib.request.build_opener(*handlers)

    req = urllib.request.Request(url, data=body, headers=sent, method=method.upper())
    # Debug logging is how a network problem gets diagnosed without a packet capture: which URL, which
    # status, how long, and how big. The URL can carry a token in a query string, so it is logged
    # path-and-host only — never the full query, never a header (Authorization lives there).
    started = time.monotonic()
    safe = urllib.parse.urlsplit(url)
    where = f"{method.upper()} {safe.scheme}://{safe.netloc}{safe.path}"
    logging.debug("%s ...", where)
    try:
        with opener.open(req, timeout=timeout) as r:
            answer = Response(r.status, r.read(), r.headers, r.geturl(), redirects.history)
        logging.debug("%s -> %s  %dB in %.0fms%s", where, answer.status_code, len(answer.content),
                      (time.monotonic() - started) * 1000,
                      f"  ({len(answer.history)} redirect(s))" if answer.history else "")
        return answer
    except urllib.error.HTTPError as e:
        # >= 400 is an answer, not a failure — the backends turn the status into the sentence to show.
        # The cookie handler has already run by now (it sits ahead of HTTPErrorProcessor), so a
        # Set-Cookie on an error response is still captured.
        try:
            content = e.read()
        finally:
            e.close()
        logging.debug("%s -> %s  %dB in %.0fms", where, e.code, len(content),
                      (time.monotonic() - started) * 1000)
        return Response(e.code, content, e.headers, e.url, redirects.history)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        # HTTPException covers the connection dying MID-RESPONSE — IncompleteRead when a proxy or a
        # load balancer drops the tail, BadStatusLine when it closes a kept-alive socket at the wrong
        # moment. Measured against a proxy that truncated a reply: without this it escapes as a raw
        # http.client.IncompleteRead, straight past cadence._request's error mapping, and reaches the
        # user as a traceback-shaped string instead of "Can't reach Cadence at ...".
        logging.debug("%s -> FAILED in %.0fms: %s", where, (time.monotonic() - started) * 1000, e)
        raise RequestError(getattr(e, "reason", None) or e) from e


def post(url, **kw):
    return request("POST", url, **kw)


class Session:
    """Headers and cookies that persist across calls. The `requests.Session` stand-in.

    Thread-safety is the same deal as before: each call is one short request and the only shared state
    is the jar, which `http.cookiejar` locks internally.
    """

    def __init__(self):
        self.headers = {}
        self.cookies = CookieJar()

    def request(self, method, url, **kw):
        kw["headers"] = {**self.headers, **(kw.get("headers") or {})}
        kw.setdefault("cookies", self.cookies)
        return request(method, url, **kw)


def selftest():
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
        # message in spotify_backend.
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
    print("httpmin self-check ok")


if __name__ == "__main__":
    selftest()
