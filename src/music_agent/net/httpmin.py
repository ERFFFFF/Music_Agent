"""Just enough of `requests` for this app, on the standard library alone.

Both backends make small JSON calls and nothing else — a dozen of them in total. `urllib.request` has
done that since Python 1.x, so `requests` was the last runtime dependency the CLI carried for work the
interpreter already ships. Removing it is the same trade the project already made for `spotipy`,
`appdirs`, `ratelimit` and `python-dotenv`: a named ceiling instead of a package.

    ponytail: this is a shim, not an HTTP library. It covers exactly what cadence.py and
    spotify.py call — GET/POST/PUT/DELETE, JSON and form bodies, query params, a cookie jar,
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


def shape(url, params, json, data, headers):
    """`(url, body, headers)` — turn the call's arguments into what goes on the wire.

    Shared with winhttp.py, which had a verbatim copy: two transports disagreeing about how a body is
    encoded is the kind of bug that only shows up on the one machine using the other one.

    `params` goes through `urlencode`, which percent-encodes the colons in `spotify:track:<id>` — the
    form Spotify's /me/library documents.
    """
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
    return url, body, sent


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
    from music_agent.net import winhttp   # local: winhttp imports FROM here, so a top-level import cycles

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
    url, body, sent = shape(url, params, json, data, headers)

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
