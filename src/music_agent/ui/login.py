"""First-run setup windows: pick a service, then sign in to it.

Three windows, all blocking on purpose — ui/tray.py runs them before it registers any hotkey, because a
hotkey that fires with nothing configured can only queue errors:

  choose_mode()    which service the hotkeys drive (Cadence account / Spotify app)
  sign_in()        Cadence username + password -> a signed-in CadenceClient
  spotify_setup()  Spotify Client ID + Secret -> config.save_spotify_credentials

None of them come back after setup: everything they collect lands in cadence_config.txt beside the app,
so a configured copy goes straight to the tray. Settings can reopen any of them.
"""

import logging
import threading

import customtkinter as ctk

from music_agent.backends.cadence import CadenceError, client_from_config, normalize_url
from music_agent.config import (DEFAULT_REDIRECT_URI, apply_proxy, config_path, load_config,
                    save_config, save_spotify_credentials)
from music_agent.ui.widgets import secret_entry

_lock = threading.Lock()


class LoginWindow:
    def __init__(self, client, cfg):
        self.client = client
        self.cfg = cfg
        self.result = None

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title("Music Agent — Sign in to Cadence")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)
        self._build()
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.root.winfo_width() // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.root.winfo_height() // 2)
        self.root.geometry(f"+{x}+{y}")
        self.root.mainloop()

    def _build(self):
        frame = ctk.CTkFrame(self.root, fg_color="transparent")
        frame.pack(padx=30, pady=25, fill="both", expand=True)

        # Header: title on the left, the gear on the right. The Cloudflare Access service token lives
        # behind that gear rather than inline — it's needed once per machine (and not at all for a
        # server that isn't behind Access), so it shouldn't sit between the user and the password.
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x")
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", anchor="w")
        ctk.CTkLabel(titles, text="Sign in to Cadence",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(titles, text="Your Cadence account — the same one you use in the web player.",
                     font=ctk.CTkFont(size=13), text_color=("gray50", "gray60")).pack(anchor="w", pady=(4, 0))
        self.gear = ctk.CTkButton(
            header, text="⚙", width=36, height=36, corner_radius=8, fg_color="transparent",
            hover_color=("gray80", "gray25"), text_color=("gray30", "gray75"),
            font=ctk.CTkFont(size=18), command=self._open_cloudflare,
        )
        self.gear.pack(side="right", anchor="n")
        # Next to the gear, because on a network with a mandatory proxy this window is the first
        # thing that fails and Settings (behind the tray icon) does not exist yet.
        ctk.CTkButton(
            header, text="Proxy", width=64, height=36, corner_radius=8, fg_color="transparent",
            hover_color=("gray80", "gray25"), text_color=("gray30", "gray75"), border_width=1,
            border_color=("gray60", "gray40"), font=ctk.CTkFont(size=12),
            command=lambda: proxy_dialog(self.root, self.cfg),
        ).pack(side="right", anchor="n", padx=(0, 8))

        # The values the gear dialog edits. Held as vars (not widgets) so _submit reads one place
        # whether or not the dialog was ever opened.
        self.cf_id = ctk.StringVar(master=self.root, value=self.cfg.get("cf_access_client_id", ""))
        self.cf_secret = ctk.StringVar(master=self.root, value=self.cfg.get("cf_access_client_secret", ""))
        self.cf_note = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=11),
                                    text_color=("gray55", "gray55"))
        self.cf_note.pack(anchor="w", pady=(6, 10))
        self._refresh_cf_note()

        card = ctk.CTkFrame(frame, corner_radius=12)
        card.pack(fill="x")

        # Pre-filled from what is already stored, decrypted. An app that made you retype a server
        # address it has known for months was treating its own config file as write-only.
        self.url = _field(card, "Server", self.cfg.get("cadence_url", ""),
                          placeholder="https://cadence.your-domain.com")
        self.user = _field(card, "Username", self.cfg.get("cadence_username", ""))
        self.password = _field(card, "Password", self.cfg.get("cadence_password", ""), secret=True)

        ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

        # A real hint label, not the entry's placeholder: customtkinter suppresses placeholder_text once
        # a textvariable is bound (verified — the field just renders blank), and "Server" alone doesn't
        # tell you it wants a full URL.
        ctk.CTkLabel(frame, text="Server example:  https://cadence.your-domain.com",
                     font=ctk.CTkFont(size=11), text_color=("gray55", "gray55")).pack(anchor="w", pady=(8, 0))

        self.error = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=12),
                                  text_color=("#b3261e", "#f2b8b5"), wraplength=380, justify="left")
        self.error.pack(anchor="w", pady=(10, 0))

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(buttons, text="Cancel", width=90, height=38, corner_radius=8,
                      fg_color="transparent", hover_color=("gray80", "gray25"),
                      text_color=("gray30", "gray70"), border_width=1, border_color=("gray60", "gray40"),
                      command=self._cancel).pack(side="right")
        self.submit = ctk.CTkButton(buttons, text="Sign in", width=110, height=38, corner_radius=8,
                                    font=ctk.CTkFont(size=13, weight="bold"), command=self._submit)
        self.submit.pack(side="right", padx=(0, 10))

        self.root.bind("<Return>", lambda _e: self._submit())

    def _refresh_cf_note(self):
        """One line under the header saying whether a service token is set — otherwise the gear looks
        decorative and a blocked login has no visible cause."""
        self.cf_note.configure(text=("⚙ Cloudflare Access service token set."
                                     if self.cf_id.get().strip() and self.cf_secret.get().strip()
                                     else "⚙ No Cloudflare Access token — set one if your server is behind Access."))

    def _open_cloudflare(self):
        cloudflare_dialog(self.root, self.cfg, self.cf_id, self.cf_secret)
        self._refresh_cf_note()

    def _submit(self):
        self.error.configure(text="")
        self.submit.configure(state="disabled", text="Signing in…")
        self.root.update_idletasks()

        url = normalize_url(self.url.get())
        if not url:
            self.error.configure(text="Enter the address of your Cadence server.")
            self.submit.configure(state="normal", text="Sign in")
            return
        # Settings are saved BEFORE the attempt so a typo'd URL is still there to fix next time, and a
        # successful login never depends on a second save.
        self.cfg["cadence_url"] = url
        self.url.set(url)
        self.cfg["cf_access_client_id"] = self.cf_id.get().strip()
        self.cfg["cf_access_client_secret"] = self.cf_secret.get().strip()
        self.cfg["mode"] = "cadence"
        try:
            save_config(self.cfg)
        except OSError:
            pass  # a read-only config dir must not block signing in for this session

        client = client_from_config(self.cfg)
        try:
            me = client.login(self.user.get().strip(), self.password.get())
        except CadenceError as e:
            self.error.configure(text=str(e))
            self.submit.configure(state="normal", text="Sign in")
            return
        logging.info(f"Signed in to Cadence as {me.get('username')}")
        # Remember the account only once it has WORKED. Storing it alongside the URL above would keep
        # a typo'd password and silently retry it on every launch, which reads as the server
        # rejecting a good account. `self.cfg` already holds the session cookie: client.login() put it
        # there through update_config, so this save cannot undo it.
        self.cfg["cadence_username"] = self.user.get().strip()
        self.cfg["cadence_password"] = self.password.get()
        try:
            save_config(self.cfg)
        except OSError:
            pass  # signed in for this session either way; the next launch just asks again
        self.result = client
        self.root.destroy()

    def _cancel(self):
        self.result = None
        self.root.destroy()


class ChooseModeWindow:
    """"How do you want to control your music?" — the first thing a fresh copy shows."""

    def __init__(self, current):
        self.result = None
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title("Music Agent — Setup")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)

        frame = ctk.CTkFrame(self.root, fg_color="transparent")
        frame.pack(padx=30, pady=25, fill="both", expand=True)
        ctk.CTkLabel(frame, text="How do you want to control your music?",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(frame, text="You can change this later in Settings.", font=ctk.CTkFont(size=13),
                     text_color=("gray50", "gray60")).pack(anchor="w", pady=(4, 18))

        self._option(frame, "Cadence account",
                     "Sign in to your Cadence server. Nothing else to set up — and it works where "
                     "Spotify itself is blocked. Keep a Cadence tab open in your browser.",
                     "cadence", primary=True)
        self._option(frame, "Spotify app (Client ID + Secret)",
                     "Control this machine's Spotify Connect device. Asks for your Spotify developer "
                     "credentials next and saves them to a file beside the app.",
                     "spotify", primary=False)

        ctk.CTkLabel(frame, text=f"Currently: {current}", font=ctk.CTkFont(size=11),
                     text_color=("gray55", "gray50")).pack(anchor="w", pady=(6, 0))

        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.root.winfo_width() // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.root.winfo_height() // 2)
        self.root.geometry(f"+{x}+{y}")
        self.root.mainloop()

    def _option(self, parent, title, blurb, mode, primary):
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(anchor="w", padx=20, pady=(14, 2))
        ctk.CTkLabel(card, text=blurb, font=ctk.CTkFont(size=12), text_color=("gray45", "gray62"),
                     wraplength=420, justify="left", anchor="w").pack(anchor="w", padx=20)
        ctk.CTkButton(
            card, text="Use this", width=110, height=34, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold" if primary else "normal"),
            fg_color=None if primary else "transparent",
            border_width=0 if primary else 1, border_color=("gray60", "gray40"),
            text_color=None if primary else ("gray20", "gray85"),
            hover_color=None if primary else ("gray80", "gray25"),
            command=lambda: self._pick(mode),
        ).pack(anchor="e", padx=20, pady=(10, 14))

    def _pick(self, mode):
        self.result = mode
        self.root.destroy()

    def _cancel(self):
        self.result = None
        self.root.destroy()


class SpotifySetupWindow:
    """Client ID + Secret, saved into cadence_config.txt — the app doing what the installer used to."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.result = False

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title("Music Agent — Spotify credentials")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)

        frame = ctk.CTkFrame(self.root, fg_color="transparent")
        frame.pack(padx=30, pady=25, fill="both", expand=True)
        ctk.CTkLabel(frame, text="Spotify credentials",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(frame, text="Create an app at developer.spotify.com/dashboard, then paste its "
                                 "Client ID and Secret. Add the redirect URI below to that app's settings.",
                     font=ctk.CTkFont(size=13), text_color=("gray50", "gray60"),
                     wraplength=430, justify="left").pack(anchor="w", pady=(4, 16))

        card = ctk.CTkFrame(frame, corner_radius=12)
        card.pack(fill="x")
        self.client_id = _field(card, "Client ID", cfg.get("spotify_client_id", ""))
        self.client_secret = _field(card, "Client Secret", cfg.get("spotify_client_secret", ""),
                                    secret=True)
        self.redirect = _field(card, "Redirect URI",
                               cfg.get("spotify_redirect_uri") or DEFAULT_REDIRECT_URI)
        ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

        self.error = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=12),
                                  text_color=("#b3261e", "#f2b8b5"), wraplength=430, justify="left")
        self.error.pack(anchor="w", pady=(10, 0))
        ctk.CTkLabel(frame, text=f"Saved to: {config_path()}", font=ctk.CTkFont(size=11),
                     text_color=("gray55", "gray50"), wraplength=430,
                     justify="left").pack(anchor="w", pady=(6, 0))

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(buttons, text="Cancel", width=90, height=38, corner_radius=8,
                      fg_color="transparent", hover_color=("gray80", "gray25"),
                      text_color=("gray30", "gray70"), border_width=1, border_color=("gray60", "gray40"),
                      command=self._cancel).pack(side="right")
        ctk.CTkButton(buttons, text="Save", width=110, height=38, corner_radius=8,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self._save).pack(side="right", padx=(0, 10))
        self.root.bind("<Return>", lambda _e: self._save())

        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.root.winfo_width() // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.root.winfo_height() // 2)
        self.root.geometry(f"+{x}+{y}")
        self.root.mainloop()

    def _save(self):
        cid, secret = self.client_id.get().strip(), self.client_secret.get().strip()
        if not cid or not secret:
            self.error.configure(text="Both the Client ID and the Client Secret are required.")
            return
        self.cfg["mode"] = "spotify"
        try:
            save_spotify_credentials(self.cfg, cid, secret,
                                     self.redirect.get().strip() or DEFAULT_REDIRECT_URI)
        except OSError as e:
            self.error.configure(text=f"Couldn't write the config file: {e}")
            return
        self.result = True
        self.root.destroy()

    def _cancel(self):
        self.result = False
        self.root.destroy()


def cloudflare_dialog(parent, cfg, id_var=None, secret_var=None):
    """The gear: Cloudflare Access service token, saved to the config immediately.

    A modal CTkToplevel over whichever window opened it (the sign-in screen or Settings) — one dialog,
    two entry points, so the token has a home both before and after you're signed in. `id_var`/
    `secret_var` are the caller's live values; they're only written on Save, so Cancel really cancels.

    Saving takes effect on the NEXT client build (the sign-in below, or the controller rebuild after
    Settings), because the headers are set when a CadenceClient is constructed.
    """
    win = ctk.CTkToplevel(parent)
    win.title("Cloudflare Access")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()
    win.attributes("-topmost", True)

    frame = ctk.CTkFrame(win, fg_color="transparent")
    frame.pack(padx=24, pady=20, fill="both", expand=True)
    ctk.CTkLabel(frame, text="Cloudflare Access",
                 font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
    ctk.CTkLabel(frame, text="Only needed if your Cadence server sits behind Cloudflare Access — it "
                             "answers apps like this one with a login page they can't complete. Create a "
                             "service token in Zero Trust → Access → Service Auth, allow it on the "
                             "Cadence application's policy, then paste it here.",
                 font=ctk.CTkFont(size=12), text_color=("gray50", "gray60"),
                 wraplength=420, justify="left").pack(anchor="w", pady=(4, 14))

    card = ctk.CTkFrame(frame, corner_radius=12)
    card.pack(fill="x")
    local_id = _field(card, "Client Id", (id_var.get() if id_var else cfg.get("cf_access_client_id", "")))
    local_secret = _field(card, "Client Secret",
                          (secret_var.get() if secret_var else cfg.get("cf_access_client_secret", "")),
                          secret=True)
    ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

    error = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=12),
                         text_color=("#b3261e", "#f2b8b5"), wraplength=420, justify="left")
    error.pack(anchor="w", pady=(8, 0))

    def save():
        cid, secret = local_id.get().strip(), local_secret.get().strip()
        if bool(cid) != bool(secret):
            # Half a token is worse than none: it would be sent, rejected, and look like a Cadence fault.
            error.configure(text="Enter both values, or clear both to send no token at all.")
            return
        cfg["cf_access_client_id"] = cid
        cfg["cf_access_client_secret"] = secret
        if id_var is not None:
            id_var.set(cid)
        if secret_var is not None:
            secret_var.set(secret)
        try:
            save_config(cfg)
        except OSError as e:
            error.configure(text=f"Couldn't save: {e}")
            return
        win.grab_release()
        win.destroy()

    def cancel():
        win.grab_release()
        win.destroy()

    buttons = ctk.CTkFrame(frame, fg_color="transparent")
    buttons.pack(fill="x", pady=(12, 0))
    ctk.CTkButton(buttons, text="Cancel", width=90, height=36, corner_radius=8, fg_color="transparent",
                  hover_color=("gray80", "gray25"), text_color=("gray30", "gray70"), border_width=1,
                  border_color=("gray60", "gray40"), command=cancel).pack(side="right")
    ctk.CTkButton(buttons, text="Save", width=100, height=36, corner_radius=8,
                  font=ctk.CTkFont(size=13, weight="bold"), command=save).pack(side="right", padx=(0, 10))
    win.protocol("WM_DELETE_WINDOW", cancel)
    win.bind("<Return>", lambda _e: save())

    win.update_idletasks()
    win.geometry(f"+{parent.winfo_x() + 60}+{parent.winfo_y() + 60}")
    parent.wait_window(win)   # blocking: the caller reads the values right after


def proxy_dialog(parent, cfg):
    """The corporate proxy, from the sign-in screen — where it is actually needed.

    Settings has the same four fields, but Settings lives behind the tray icon and the tray icon does
    not exist yet on a first run: the sign-in window is the first thing a fresh copy shows, and on a
    network that mandates a proxy it is also the first thing to fail. Without this, the only way to
    configure the proxy was to finish the sign-in it was blocking.

    Saved settings are applied IMMEDIATELY (config.apply_proxy), so the very next "Sign in" goes
    through the proxy rather than a restart later.
    """
    win = ctk.CTkToplevel(parent)
    win.title("Corporate proxy")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()
    win.attributes("-topmost", True)

    frame = ctk.CTkFrame(win, fg_color="transparent")
    frame.pack(padx=24, pady=20, fill="both", expand=True)
    ctk.CTkLabel(frame, text="Corporate proxy — optional",
                 font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
    ctk.CTkLabel(frame, text="Leave empty to connect directly, which is what almost every machine "
                             "wants. Fill this in if your network forces traffic through a proxy — the "
                             "sign it does is “cannot resolve the host”, which looks like a DNS fault "
                             "and is not one. The username and password are stored encrypted, like "
                             "your account.",
                 font=ctk.CTkFont(size=12), text_color=("gray50", "gray60"),
                 wraplength=430, justify="left").pack(anchor="w", pady=(4, 14))

    card = ctk.CTkFrame(frame, corner_radius=12)
    card.pack(fill="x")
    address = _field(card, "Proxy address", cfg.get("proxy_url", ""),
                     placeholder="proxy.company.com:8080")
    user = _field(card, "Username", cfg.get("proxy_user", ""))
    password = _field(card, "Password", cfg.get("proxy_password", ""), secret=True)

    row = ctk.CTkFrame(card, fg_color="transparent")
    row.pack(fill="x", padx=20, pady=(2, 12))
    auth = ctk.StringVar(master=row, value="on" if (cfg.get("proxy_auth") or "").strip() else "off")
    ctk.CTkCheckBox(row, text="Sign in to the proxy as my Windows user (NTLM / Kerberos)",
                    variable=auth, onvalue="on", offvalue="off", font=ctk.CTkFont(size=12),
                    checkbox_width=18, checkbox_height=18).pack(side="left")

    error = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=12),
                         text_color=("#b3261e", "#f2b8b5"), wraplength=430, justify="left")
    error.pack(anchor="w", pady=(8, 0))

    def save():
        cfg["proxy_url"] = address.get().strip()
        cfg["proxy_user"] = user.get().strip()
        cfg["proxy_password"] = password.get()
        cfg["proxy_auth"] = "current-user" if auth.get() == "on" else ""
        try:
            save_config(cfg)
        except OSError as e:
            error.configure(text=f"Couldn't save: {e}")
            return
        # Now, not at the next launch: whoever just typed this in is about to press Sign in, and
        # doing that through the old (absent) proxy would blame the address for a timing problem.
        applied = apply_proxy(cfg)
        logging.info("Proxy settings saved: %s", ", ".join(sorted(applied)) or "none (direct)")
        win.grab_release()
        win.destroy()

    def cancel():
        win.grab_release()
        win.destroy()

    buttons = ctk.CTkFrame(frame, fg_color="transparent")
    buttons.pack(fill="x", pady=(12, 0))
    ctk.CTkButton(buttons, text="Cancel", width=90, height=36, corner_radius=8, fg_color="transparent",
                  hover_color=("gray80", "gray25"), text_color=("gray30", "gray70"), border_width=1,
                  border_color=("gray60", "gray40"), command=cancel).pack(side="right")
    ctk.CTkButton(buttons, text="Save", width=100, height=36, corner_radius=8,
                  font=ctk.CTkFont(size=13, weight="bold"), command=save).pack(side="right", padx=(0, 10))
    win.protocol("WM_DELETE_WINDOW", cancel)
    win.bind("<Return>", lambda _e: save())

    win.update_idletasks()
    win.geometry(f"+{parent.winfo_x() + 60}+{parent.winfo_y() + 60}")
    parent.wait_window(win)


def _field(parent, label, value, secret=False, placeholder=None):
    """One labelled entry row, shared by every window here. `secret=True` masks it and adds the eye.

    `master=row` is not optional. A StringVar with no master attaches to tkinter's `_default_root`,
    which is whichever root was created FIRST and is only released when that root is destroyed. Opened
    from the tray, the Settings root is merely withdrawn, so the variable ends up owned by a different
    Tcl interpreter than the entry widget: the field renders blank and nothing typed into it is ever
    read back. At launch the setup windows destroy each root before the next opens, which is why this
    only ever went wrong via Settings.
    """
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=20, pady=6)
    ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=13), width=120, anchor="w").pack(side="left")
    var = ctk.StringVar(master=row, value=value)
    if secret:
        ctk.CTkEntry(row, textvariable=var, width=260, height=34, corner_radius=8, show="•",
                     placeholder_text=placeholder, font=ctk.CTkFont(size=13)).pack(side="left")
    else:
        ctk.CTkEntry(row, textvariable=var, width=260, height=34, corner_radius=8,
                     placeholder_text=placeholder, font=ctk.CTkFont(size=13)).pack(side="left")
    return var


def choose_mode(cfg=None):
    """Ask which service to control. Returns "cadence" / "spotify", or None if the user closed it."""
    cfg = cfg or load_config()
    with _lock:
        return ChooseModeWindow(cfg.get("mode", "cadence")).result


def spotify_setup(cfg=None):
    """Prompt for Spotify credentials and write them. True when saved."""
    cfg = cfg or load_config()
    with _lock:
        return SpotifySetupWindow(cfg).result


def sign_in(cfg=None):
    """Show the Cadence login and return a signed-in client, or None if the user cancelled."""
    cfg = cfg or load_config()
    with _lock:
        return LoginWindow(client_from_config(cfg), cfg).result
