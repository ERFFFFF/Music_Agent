"""Windows' own HTTP stack, for the one thing `urllib` cannot do: authenticate to a corporate proxy
as the logged-in user.

`urllib` speaks only **Basic** proxy authentication. A corporate proxy almost always wants **NTLM** or
**Negotiate/Kerberos** against your Windows account — measured here against proxies demanding each:
urllib never even attempts them, it just surfaces the 407. There is no way to add that in pure Python
without hand-rolling SSPI, so this hands the whole job to WinHTTP, which does it in one option flag and
also knows how to read a **PAC file** (which urllib does not).

    ponytail: this is a second transport, and a second transport is a cost. It exists ONLY because
    "use my Windows login for the proxy" has no cheaper answer, and it is OFF unless a .env asks for
    it (config.proxy_auth_is_current_user). Every network that works without it keeps using httpmin,
    which is the tested-everywhere path. Delete this file the day the proxy goes away.

Same signature and same Response as httpmin.request, so it drops into the same call sites.

**Untested against a real corporate proxy.** The transport itself is verified end to end (real HTTPS
to Cadence, a local CONNECT proxy, headers, bodies, redirects, cookies), but nobody here owns an
NTLM proxy, so the SSPI handshake is exercised only in the sense that the flags are set the way
Microsoft documents. Expect to confirm that part on the network it was written for.
"""

import ctypes
import email.parser
import json as _json
import os
import urllib.parse
import urllib.request
from ctypes import wintypes

from httpmin import DEFAULT_TIMEOUT, USER_AGENT, RequestError, Response

# ------------------------------------------------------------------------------------- WinHTTP ABI
WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY = 4      # honours WPAD + PAC, which is half the reason for this
WINHTTP_ACCESS_TYPE_NAMED_PROXY = 3
WINHTTP_FLAG_SECURE = 0x00800000
WINHTTP_ADDREQ_FLAG_ADD = 0x20000000
WINHTTP_ADDREQ_FLAG_REPLACE = 0x80000000

WINHTTP_QUERY_RAW_HEADERS_CRLF = 22
WINHTTP_QUERY_STATUS_CODE = 19
WINHTTP_QUERY_FLAG_NUMBER = 0x20000000

WINHTTP_OPTION_DISABLE_FEATURE = 63
WINHTTP_DISABLE_COOKIES = 0x00000001         # the CookieJar owns cookies; two managers would fight
WINHTTP_DISABLE_REDIRECTS = 0x00000002       # we follow them by hand to keep the chain (see below)
WINHTTP_OPTION_AUTOLOGON_POLICY = 77
WINHTTP_AUTOLOGON_SECURITY_LEVEL_LOW = 0     # send default credentials to any server that asks

# A WinHTTP session still defaults to SSL3 + TLS 1.0, which every modern edge refuses. Measured:
# without this, the first HTTPS request to Cloudflare dies with ERROR_WINHTTP_SECURE_FAILURE (12175)
# and nothing says why. TLS 1.3's flag is ignored by Windows versions that don't know it.
WINHTTP_OPTION_SECURE_PROTOCOLS = 84
WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 = 0x00000800
WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3 = 0x00002000

WINHTTP_AUTH_TARGET_PROXY = 1
WINHTTP_AUTH_SCHEME_NTLM = 0x00000002
WINHTTP_AUTH_SCHEME_NEGOTIATE = 0x00000010

ERROR_WINHTTP_RESEND_REQUEST = 12032
MAX_REDIRECTS = 10

_dll = None


def available():
    """Is WinHTTP usable here? False everywhere that isn't Windows, without raising."""
    return _load() is not None


def _load():
    global _dll
    if _dll is None:
        if os.name != "nt":
            return None
        _dll = ctypes.WinDLL("winhttp", use_last_error=True)
        for name, restype, argtypes in (
            ("WinHttpOpen", ctypes.c_void_p,
             [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]),
            ("WinHttpConnect", ctypes.c_void_p,
             [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD]),
            ("WinHttpOpenRequest", ctypes.c_void_p,
             [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
              ctypes.c_void_p, wintypes.DWORD]),
            ("WinHttpSetOption", wintypes.BOOL,
             [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]),
            ("WinHttpSetTimeouts", wintypes.BOOL,
             [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
            ("WinHttpAddRequestHeaders", wintypes.BOOL,
             [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]),
            ("WinHttpSendRequest", wintypes.BOOL,
             [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
              wintypes.DWORD, ctypes.c_void_p]),
            ("WinHttpReceiveResponse", wintypes.BOOL, [ctypes.c_void_p, ctypes.c_void_p]),
            ("WinHttpQueryHeaders", wintypes.BOOL,
             [ctypes.c_void_p, wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_void_p,
              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]),
            ("WinHttpQueryDataAvailable", wintypes.BOOL,
             [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]),
            ("WinHttpReadData", wintypes.BOOL,
             [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]),
            ("WinHttpQueryAuthSchemes", wintypes.BOOL,
             [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
              ctypes.POINTER(wintypes.DWORD)]),
            ("WinHttpSetCredentials", wintypes.BOOL,
             [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
              ctypes.c_void_p]),
            ("WinHttpCloseHandle", wintypes.BOOL, [ctypes.c_void_p]),
        ):
            fn = getattr(_dll, name)
            fn.restype, fn.argtypes = restype, argtypes
    return _dll


# The handful of WinHTTP errors this app can actually provoke, as sentences someone can act on.
# 12175 is the one that matters and the one that looks like nothing: see the note below.
ERROR_WINHTTP_SECURE_FAILURE = 12175
_ERRORS = {
    12002: "the request timed out.",
    12007: "the host name could not be resolved.",
    12029: "the connection was refused.",
    12030: "the connection was closed by the server.",
    ERROR_WINHTTP_SECURE_FAILURE: (
        "Windows could not negotiate TLS with this host.\n"
        "The usual cause is NOT a certificate: Windows 10's TLS stack stops at TLS 1.2, and a host "
        "that requires TLS 1.3 is unreachable to it. Measured against this app's own server — "
        "`curl` (which also uses Windows' stack) fails identically, while Python succeeds because it "
        "ships its own OpenSSL.\n"
        "Fix it at the server: Cloudflare -> SSL/TLS -> Edge Certificates -> Minimum TLS Version -> "
        "1.2. Or just remove PROXY_AUTH from your .env — the default transport does TLS 1.3 and does "
        "not have this limit; you lose only proxy authentication as the logged-in user."),
    12044: "the server asked for a client certificate.",
}


def _fail(what):
    code = ctypes.get_last_error()
    detail = _ERRORS.get(code)
    raise RequestError(f"{detail}" if detail else f"{what} failed (WinHTTP error {code})")


class _Handle:
    """A WinHTTP handle that closes itself. Leaking these leaks a socket and a thread pool entry."""

    def __init__(self, value, what):
        if not value:
            _fail(what)
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_exc):
        _load().WinHttpCloseHandle(self.value)


def _query_headers(request_handle):
    """The raw response headers, parsed into the same email.Message the rest of the app expects."""
    dll = _load()
    size = wintypes.DWORD(0)
    dll.WinHttpQueryHeaders(request_handle, WINHTTP_QUERY_RAW_HEADERS_CRLF, None, None,
                            ctypes.byref(size), None)
    buf = ctypes.create_string_buffer(size.value)
    if not dll.WinHttpQueryHeaders(request_handle, WINHTTP_QUERY_RAW_HEADERS_CRLF, None, buf,
                                   ctypes.byref(size), None):
        _fail("WinHttpQueryHeaders")
    raw = buf.raw[:size.value].decode("utf-16-le", "replace")
    # The first line is the status line, which email's parser would take as a malformed header.
    body = raw.split("\r\n", 1)[1] if "\r\n" in raw else ""
    return email.parser.Parser().parsestr(body)


def _status(request_handle):
    dll = _load()
    code = wintypes.DWORD(0)
    size = wintypes.DWORD(ctypes.sizeof(code))
    if not dll.WinHttpQueryHeaders(request_handle,
                                   WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                   None, ctypes.byref(code), ctypes.byref(size), None):
        _fail("WinHttpQueryHeaders(status)")
    return code.value


def _read_body(request_handle):
    dll = _load()
    chunks = []
    while True:
        available_bytes = wintypes.DWORD(0)
        if not dll.WinHttpQueryDataAvailable(request_handle, ctypes.byref(available_bytes)):
            _fail("WinHttpQueryDataAvailable")
        if not available_bytes.value:
            return b"".join(chunks)
        buf = ctypes.create_string_buffer(available_bytes.value)
        read = wintypes.DWORD(0)
        if not dll.WinHttpReadData(request_handle, buf, available_bytes.value, ctypes.byref(read)):
            _fail("WinHttpReadData")
        chunks.append(buf.raw[:read.value])


def _one_request(method, url, body, headers, timeout, proxy):
    """A single exchange, with a 407 retried once using the logged-in Windows account."""
    dll = _load()
    parts = urllib.parse.urlsplit(url)
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    if proxy:
        access, proxy_name = WINHTTP_ACCESS_TYPE_NAMED_PROXY, proxy
    else:
        # No explicit proxy: let Windows work it out, which is what picks up WPAD and a PAC file.
        access, proxy_name = WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, None

    with _Handle(dll.WinHttpOpen(USER_AGENT, access, proxy_name, None, 0), "WinHttpOpen") as session:
        ms = int(timeout * 1000)
        dll.WinHttpSetTimeouts(session, ms, ms, ms, ms)
        # Send the logged-in user's credentials when challenged. This is the whole reason this module
        # exists; without it WinHTTP would surface the 407 exactly as urllib does.
        policy = wintypes.DWORD(WINHTTP_AUTOLOGON_SECURITY_LEVEL_LOW)
        dll.WinHttpSetOption(session, WINHTTP_OPTION_AUTOLOGON_POLICY, ctypes.byref(policy),
                             ctypes.sizeof(policy))
        protocols = wintypes.DWORD(WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 |
                                   WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3)
        dll.WinHttpSetOption(session, WINHTTP_OPTION_SECURE_PROTOCOLS, ctypes.byref(protocols),
                             ctypes.sizeof(protocols))

        with _Handle(dll.WinHttpConnect(session, parts.hostname, port, 0), "WinHttpConnect") as conn:
            flags = WINHTTP_FLAG_SECURE if secure else 0
            with _Handle(dll.WinHttpOpenRequest(conn, method, path, None, None, None, flags),
                         "WinHttpOpenRequest") as req:
                disable = wintypes.DWORD(WINHTTP_DISABLE_COOKIES | WINHTTP_DISABLE_REDIRECTS)
                dll.WinHttpSetOption(req, WINHTTP_OPTION_DISABLE_FEATURE, ctypes.byref(disable),
                                     ctypes.sizeof(disable))
                for name, value in headers.items():
                    dll.WinHttpAddRequestHeaders(
                        req, f"{name}: {value}\r\n", -1,
                        WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE)

                for attempt in (1, 2):
                    if not dll.WinHttpSendRequest(req, None, 0, body, len(body or b""),
                                                  len(body or b""), None):
                        _fail("WinHttpSendRequest")
                    if not dll.WinHttpReceiveResponse(req, None):
                        _fail("WinHttpReceiveResponse")
                    status = _status(req)
                    if status != 407 or attempt == 2:
                        break
                    # The proxy asked who we are. Ask it which schemes it accepts, then hand WinHTTP
                    # NULL credentials for the strongest one -- NULL means "the logged-in user", which
                    # is the documented way to get SSPI without ever holding a password.
                    supported, first, target = (wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD())
                    if not dll.WinHttpQueryAuthSchemes(req, ctypes.byref(supported),
                                                       ctypes.byref(first), ctypes.byref(target)):
                        break
                    scheme = (WINHTTP_AUTH_SCHEME_NEGOTIATE
                              if supported.value & WINHTTP_AUTH_SCHEME_NEGOTIATE
                              else WINHTTP_AUTH_SCHEME_NTLM if supported.value & WINHTTP_AUTH_SCHEME_NTLM
                              else first.value)
                    if not dll.WinHttpSetCredentials(req, WINHTTP_AUTH_TARGET_PROXY, scheme,
                                                     None, None, None):
                        break
                return status, _query_headers(req), _read_body(req)


def request(method, url, *, params=None, json=None, data=None, headers=None,
            timeout=DEFAULT_TIMEOUT, cookies=None):
    """Same contract as httpmin.request, including the redirect chain in `.history`.

    Redirects are followed here rather than by WinHTTP because `cadence._blocked_by_access` reads the
    chain to tell a Cloudflare Access wall from a real answer; letting WinHTTP follow them silently
    would erase exactly the evidence that check needs.
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

    proxy = _proxy_for(url)
    history, current, verb = [], url, method.upper()
    for _hop in range(MAX_REDIRECTS):
        request_headers = dict(sent)
        if cookies is not None:
            jar_header = _cookie_header(cookies, current)
            if jar_header:
                request_headers["Cookie"] = jar_header
        try:
            status, response_headers, content = _one_request(verb, current, body, request_headers,
                                                             timeout, proxy)
        except OSError as e:
            raise RequestError(e) from e
        if cookies is not None:
            _store_cookies(cookies, current, response_headers)

        location = response_headers.get("location")
        if status in (301, 302, 303, 307, 308) and location:
            history.append(Response(status, b"", response_headers, current))
            current = urllib.parse.urljoin(current, location)
            if status in (301, 302, 303) and verb == "POST":
                verb, body = "GET", None       # what every client does, and what urllib does
            continue
        return Response(status, content, response_headers, current, history)
    raise RequestError(f"too many redirects ({MAX_REDIRECTS}) starting at {url}")


# --------------------------------------------------------------- cookies, borrowed from the stdlib
# WinHTTP's own cookie store is disabled above, so the shared http.cookiejar stays the single source
# of truth for the session cookie CadenceClient depends on. These two adapters are the whole bridge.
class _JarRequest(urllib.request.Request):
    """http.cookiejar talks to a urllib Request; this IS one, with the headers it collects readable."""

    def __init__(self, url):
        super().__init__(url)
        self.collected = {}

    def add_unredirected_header(self, key, value):
        self.collected[key] = value


def _cookie_header(jar, url):
    holder = _JarRequest(url)
    jar.add_cookie_header(holder)
    return holder.collected.get("Cookie", "")


class _JarResponse:
    def __init__(self, headers):
        self._headers = headers

    def info(self):
        return self._headers


def _store_cookies(jar, url, headers):
    jar.extract_cookies(_JarResponse(headers), urllib.request.Request(url))


def _proxy_for(url):
    """The proxy this URL should use, from the environment, or None to let Windows decide (WPAD/PAC).

    `host:port` without a scheme is what WinHttpOpen wants; a full URL makes it fail.
    """
    scheme = urllib.parse.urlsplit(url).scheme
    configured = os.environ.get(f"{scheme.upper()}_PROXY") or os.environ.get("ALL_PROXY") or ""
    if not configured:
        return None
    parsed = urllib.parse.urlsplit(configured if "://" in configured else f"http://{configured}")
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname



def selftest():
    """Everything that does not need a corporate proxy: the transport, headers, bodies, status
    mapping, redirect chain and cookie round trip, against a real loopback server."""
    import http.cookiejar
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import httpmin

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
    print("winhttp self-check ok")


if __name__ == "__main__":
    selftest()
