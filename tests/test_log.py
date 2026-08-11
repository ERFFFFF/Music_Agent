"""Checks for `music_agent.log` — moved here from the module's own selftest()."""

from music_agent.log import *  # noqa: F401,F403 — the public surface under test
from music_agent.log import (  # noqa: F401 — including the private names it exercises
    CAPACITY, _lock, _written, clear, install, logging, since)
from music_agent import log


def test_log():
    """The two things that break silently: losing lines across the ring buffer's wrap, and handing a
    reader the same line twice."""
    global _written
    clear()
    with _lock:
        _written = 0
    install(logging.DEBUG)
    log = logging.getLogger("demo")

    lines, cursor = since()
    assert lines == [] and cursor == 0, (lines, cursor)

    log.info("first")
    log.warning("second")
    lines, cursor = since()
    assert len(lines) == 2 and "first" in lines[0] and "WARNING" in lines[1], lines
    assert "INFO" in lines[0] and lines[0].count(":") >= 2, "the line needs a level and a clock"

    # a second reader with the cursor sees only what is new — the property the Logs page relies on to
    # append rather than redraw
    fresh, cursor2 = since(cursor)
    assert fresh == [], fresh
    log.error("third")
    fresh, cursor3 = since(cursor2)
    assert len(fresh) == 1 and "third" in fresh[0], fresh

    # ...and a reader that went away for a while still gets everything it missed, in order
    for n in range(5):
        log.info("burst %d", n)
    missed, _ = since(cursor3)
    assert len(missed) == 5 and "burst 4" in missed[-1], missed

    # the buffer is bounded, and overflowing it must not resurrect old lines or repeat new ones
    clear()
    for n in range(CAPACITY + 50):
        log.debug("overflow %d", n)
    everything, _ = since()
    assert len(everything) == CAPACITY, len(everything)
    # 2050 logged into a 2000-deep buffer keeps 50..2049: the OLDEST survivor is the giveaway that
    # nothing was dropped from the middle and nothing stale was kept.
    assert everything[0].endswith("overflow 50"), everything[0]
    assert everything[-1].endswith(f"overflow {CAPACITY + 49}"), everything[-1]
    assert len(set(everything)) == len(everything), "a line was handed out twice"

    # An unformattable record becomes a note rather than an exception at whoever called log.info().
    # Handed straight to OUR handler instead of through logging.info(): every other handler on the
    # root logger would try to format it too, and pytest's does — so going through the global logger
    # would be testing pytest's error handling, not this handler's.
    class Exploding:
        def __repr__(self):
            raise RuntimeError("boom")

    clear()
    handler = install()
    handler.emit(logging.LogRecord("t", logging.INFO, __file__, 1, "%s", (Exploding(),), None))
    lines, _ = since()
    assert len(lines) == 1 and "unformattable" in lines[0], lines

    clear()
