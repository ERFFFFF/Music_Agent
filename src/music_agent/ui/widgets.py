"""The one widget both windows needed: a masked entry with an eye that reveals it.

Lives in its own module because `ui/settings.py` and `ui/login.py` both build secret fields and
neither should have to import the other — settings.py deliberately keeps `login` out of its
import-time graph (it pulls in the network client), and duplicating a toggle in two files is how the
two copies end up behaving differently.

The glyphs are BMP on purpose. Tcl 8.6 — what Python ships — cannot hold a character above U+FFFF at
all, so the obvious 👁 (U+1F441) raises `character U+1F441 is above the range allowed by Tcl` the
moment the button is created. ◉ and ⌽ are U+25C9 / U+233D, render in Segoe UI, and were checked on
screen rather than assumed.
"""

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
    return entry
