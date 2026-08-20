"""The bits every window in the app needs: how to build a root, how to place it, and the masked entry.

Lives in its own module because `ui/settings.py` and `ui/login.py` both build secret fields and
neither should have to import the other — settings.py deliberately keeps `login` out of its
import-time graph (it pulls in the network client), and duplicating a toggle in two files is how the
two copies end up behaving differently.

The glyphs are BMP on purpose. Tcl 8.6 — what Python ships — cannot hold a character above U+FFFF at
all, so the obvious 👁 (U+1F441) raises `character U+1F441 is above the range allowed by Tcl` the
moment the button is created. ◉ and ⌽ are U+25C9 / U+233D, render in Segoe UI, and were checked on
screen rather than assumed.
"""

import logging
import tkinter

import customtkinter as ctk

MASK = "•"
EYE_SHOW = "◉"      # masked right now: click to look
EYE_HIDE = "⌽"      # visible right now: click to put the dots back


def secret_entry(row, var, width=260, height=34, font_size=13):
    """A password box + eye button, packed left-to-right into `row`. Returns the entry.

    `var` is the caller's StringVar, so reading the value back is unchanged — this only decides what
    the screen shows. Masked to start with: a saved password must not be readable over a shoulder
    just because a window opened.
    """
    # No placeholder_text: customtkinter clears `show` while a placeholder is on screen, and the
    # toggle below reads `show` as the state -- an empty field would report itself as revealed. A
    # password box has nothing useful to say as a hint anyway.
    entry = ctk.CTkEntry(row, textvariable=var, width=width, height=height, corner_radius=8,
                         show=MASK, font=ctk.CTkFont(size=font_size))
    entry.pack(side="left")

    button = ctk.CTkButton(row, text=EYE_SHOW, width=34, height=height, corner_radius=8,
                           fg_color="transparent", hover_color=("gray80", "gray25"),
                           text_color=("gray30", "gray75"), font=ctk.CTkFont(size=15))

    def toggle():
        # cget("show") is the state itself — no second variable to fall out of step with the widget.
        masked = entry.cget("show") != ""
        entry.configure(show="" if masked else MASK)
        button.configure(text=EYE_HIDE if masked else EYE_SHOW)

    button.configure(command=toggle)
    button.pack(side="left", padx=(6, 0))
    entry._eye = button        # so set_enabled() below can grey the pair together
    return entry


# What "greyed out" has to look like. `state="disabled"` alone barely changes a CTkEntry on the dark
# theme — the text stays as bright as a live field, so the box reads as editable and simply refuses
# to take a keystroke, which is worse than either state on its own.
DISABLED_TEXT = ("gray55", "gray45")
DISABLED_BORDER = ("gray75", "gray28")


def set_enabled(entry, enabled):
    """Enable or disable an entry, and make it LOOK it. Greys its eye button too, if it has one.

    The live colours are read off the widget the first time and kept, so this restores whatever the
    theme actually uses rather than a hardcoded guess at it.
    """
    if not hasattr(entry, "_live_colors"):
        entry._live_colors = (entry.cget("text_color"), entry.cget("border_color"))
    text_color, border_color = entry._live_colors
    entry.configure(state="normal" if enabled else "disabled",
                    text_color=text_color if enabled else DISABLED_TEXT,
                    border_color=border_color if enabled else DISABLED_BORDER)
    eye = getattr(entry, "_eye", None)
    if eye is not None:
        eye.configure(state="normal" if enabled else "disabled")


def new_root(title):
    """A CTk root that behaves. All four windows in this app build their own, on their own thread, and
    each was missing the same three things — every one of which fails as "the window just did not
    appear", with nothing written down anywhere:

    * **report_callback_exception.** Tk hands it every exception raised inside a callback, and the
      default prints to a stderr that does not exist in the --noconsole build.
    * **tkinter._default_root.** `CTkFont()` takes no master and resolves it. A window built while an
      older root is still alive would otherwise construct its fonts against another (possibly dead)
      Tcl interpreter, which raises mid-build. Same hazard the StringVar `master=` note guards
      against, one level up.
    * **placement** — see center() below.
    """
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.report_callback_exception = lambda exc, val, tb: logging.error(
        "Tk callback failed in %r", title, exc_info=(exc, val, tb))
    tkinter._default_root = root
    root.title(title)
    root.resizable(False, False)
    return root


def center(root):
    """Place the window by its REQUESTED size, then bring it to the front once it exists.

    `winfo_width()` on a window that has not been mapped yet is **1**, so `screenwidth/2 - width/2`
    put the top-left corner at the exact centre of the PRIMARY monitor and the window hung off the
    bottom-right — on a multi-monitor desktop, sometimes entirely off-screen. The lift is scheduled
    rather than called because anything done before `mainloop()` is undone when the window is finally
    mapped, and a fresh Tk root does not come forward over a full-screen browser on its own.
    """
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    y = max(0, (root.winfo_screenheight() - h) // 3)
    root.geometry(f"+{x}+{y}")

    def raise_it():
        try:
            root.deiconify()
            root.attributes("-topmost", True)
            root.after(200, lambda: root.attributes("-topmost", False))
            root.lift()
            root.focus_force()
        except Exception as e:  # noqa: BLE001 — a window mid-destroy must not take the app down
            logging.warning("could not raise %r: %s", title_of(root), e)

    root.after(50, raise_it)
    logging.info("window %r at +%d+%d (%dx%d)", title_of(root), x, y, w, h)


def title_of(root):
    """The window's title, or "?" — only ever used in a log line, so it must not be able to raise."""
    try:
        return root.title()
    except Exception:  # noqa: BLE001
        return "?"
