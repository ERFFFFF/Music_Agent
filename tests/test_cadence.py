"""Checks for `music_agent.backends.cadence` — moved here from the module's own selftest()."""

from music_agent.backends.cadence import *  # noqa: F401,F403 — the public surface under test
from music_agent.backends.cadence import (  # noqa: F401 — including the private names it exercises
    CadenceClient, CadenceController, CadenceError, _blocked_by_access, normalize_url, sys, time)
import music_agent.backends.cadence as MODULE


def test_cadence():
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
