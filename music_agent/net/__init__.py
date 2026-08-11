"""HTTP for this app: one Response type, two transports.

`httpmin` (stdlib urllib) is the default and the one everything is tested against. `winhttp` exists
only to authenticate to a corporate proxy as the logged-in Windows user, which urllib cannot do; it
is off unless a .env asks for it, and `httpmin.use_windows_transport()` is the single switch.

Import the names from here rather than reaching for a transport by name -- callers should not care
which one is in effect, and that is the whole point of the switch being in one place.
"""

from music_agent.net.httpmin import (DEFAULT_TIMEOUT, USER_AGENT, CookieJar, RequestError, Response,
                                     Session, post, request, shape, use_windows_transport)

__all__ = ["DEFAULT_TIMEOUT", "USER_AGENT", "CookieJar", "RequestError", "Response", "Session",
           "post", "request", "shape", "use_windows_transport"]
