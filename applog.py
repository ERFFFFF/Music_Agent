"""The app's log, kept in memory and nowhere else.

Every front end wants the same thing for a different reason: the CLI prints it as it happens, and the
tray app shows it on a Logs page. Both read this one buffer, so what you see in the window is exactly
what the CLI would have printed.

**Deliberately no file.** A log file on disk is a support burden (where is it? how big? who deletes
it?) and, for this app, a privacy question — the lines name your server and your account. The buffer
dies with the process, which is the behaviour asked for and the one that needs no maintenance.

    ponytail: a deque and a counter, not a logging framework. `logging` already does levels,
    formatting and thread-safe dispatch; this is only the handler that keeps the last N lines and a
    way to ask "what is new since I last looked".

Readers POLL rather than being called back. A hotkey fires on the keyboard thread and every HTTP call
logs from wherever it runs, but Tk may only be touched from the thread that owns the window — so a
callback straight into a widget is a crash waiting for the right timing. `since()` lets the window
ask on its own thread, on a timer, and that is the whole synchronisation story.
"""

import collections
import logging
import threading

CAPACITY = 2000          # ~30 minutes of chatty debug logging; a screenful is 40
FORMAT = "%(asctime)s  %(levelname)-7s %(message)s"
DATEFMT = "%H:%M:%S"

_lock = threading.Lock()
_entries = collections.deque(maxlen=CAPACITY)
_written = 0             # total ever appended, so a reader can tell what it has already seen
_handler = None


class _Buffer(logging.Handler):
    """Formats each record once, on the thread that logged it, and keeps the string.

    Formatting here rather than in `since()` matters: a record holds references to its arguments, and
    a controller's message can name an object whose repr changes (or raises) later. The line is the
    fact worth keeping.
    """

    def emit(self, record):
        global _written
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001 — a broken log line must never take down what it logs about
            line = f"{record.levelname} <unformattable log record>"
        with _lock:
            _written += 1
            _entries.append((_written, line))


def install(level=logging.DEBUG):
    """Start capturing. Safe to call repeatedly — every entry point does, and only the first counts."""
    global _handler
    if _handler is None:
        _handler = _Buffer()
        _handler.setFormatter(logging.Formatter(FORMAT, DATEFMT))
        logging.getLogger().addHandler(_handler)
    _handler.setLevel(level)
    root = logging.getLogger()
    # Only ever LOWER the root level. main.py's initialize_logging() sets up its own file+console
    # handlers at INFO; raising the bar here would silence them, and a Logs page is not worth
    # breaking someone's existing log with.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return _handler


def since(seen=0):
    """`(lines, cursor)` — everything logged after `seen`, and the cursor to pass in next time.

    Pass 0 to get the whole history, which is what a Logs page wants when it opens: the interesting
    lines are usually the ones from before someone thought to look.
    """
    with _lock:
        lines = [line for number, line in _entries if number > seen]
        return lines, _written


def clear():
    with _lock:
        _entries.clear()


def demo():
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

    # an unformattable record is logged as a note, not raised at whoever called log.info()
    class Exploding:
        def __repr__(self):
            raise RuntimeError("boom")

    clear()
    log.info("%s", Exploding())
    lines, _ = since()
    assert len(lines) == 1 and "unformattable" in lines[0], lines

    clear()
    print("applog self-check ok")


if __name__ == "__main__":
    demo()
