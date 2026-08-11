import threading
import tkinter

import customtkinter as ctk
import keyboard

from music_agent import log
from music_agent import config as config_module

from music_agent.backends.cadence import client_from_config
from music_agent.config import (load_config, save_config, find_icon, env_config, env_path, DEFAULT_HOTKEYS,
                    ENV_HOTKEY_PREFIX, MODES)

_MODIFIERS = frozenset({
    "ctrl", "alt", "shift", "windows",
    "left ctrl", "left alt", "left shift", "left windows",
    "right ctrl", "right alt", "right shift", "right windows",
})

ACTION_LABELS = {
    "play_pause": "Play / Pause",
    "next_track": "Next Track",
    "previous_track": "Previous Track",
    "like_unlike": "Like / Unlike",
    "show_current": "Show Current Song",
}

# Prevent multiple settings windows
_window_open = False
_window_lock = threading.Lock()


class SettingsWindow:
    def __init__(self, on_save_callback=None):
        global _window_open
        with _window_lock:
            if _window_open:
                return
            _window_open = True

        self.on_save_callback = on_save_callback
        self.config = load_config()
        self.hotkey_vars = {}
        self._capturing = False
        self._capture_cancelled = False
        self._capture_hook = None

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title("Music Agent \u2014 Settings")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Window icon
        icon_path = find_icon()
        if icon_path:
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

        self._build_ui()

        # Center window on screen
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")

        self.root.mainloop()

    def _build_ui(self):
        # Two tabs, because the Logs page is a different job from the settings and does not want to
        # push the Save button off the bottom of a window that cannot be resized.
        tabs = ctk.CTkTabview(self.root, width=600, height=600, corner_radius=12)
        tabs.pack(padx=20, pady=(10, 0), fill="both", expand=True)
        tabs.add("Settings")
        tabs.add("Logs")
        self._build_logs_tab(tabs.tab("Logs"))

        # Save/Cancel are packed to the BOTTOM of the tab first, so they reserve their strip before
        # anything else claims the space -- and they live OUTSIDE the scroll area, because a Save
        # button you have to scroll to find is a Save button people miss. Adding the proxy card
        # pushed the last hotkey row and this whole bar off the bottom of a non-resizable window;
        # measured on a screenshot, which is the only way that kind of bug is ever noticed.
        self.button_area = ctk.CTkFrame(tabs.tab("Settings"), fg_color="transparent")
        self.button_area.pack(side="bottom", fill="x", padx=12, pady=(4, 10))

        # Main container: scrollable, so a card added later cannot clip the window again.
        main_frame = ctk.CTkScrollableFrame(tabs.tab("Settings"), fg_color="transparent")
        main_frame.pack(side="top", padx=6, pady=5, fill="both", expand=True)

        # Header
        header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 20))

        ctk.CTkLabel(
            header_frame,
            text="Keybind Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            header_frame,
            text="Configure your global hotkeys for Music Agent",
            font=ctk.CTkFont(size=13),
            text_color=("gray50", "gray60"),
        ).pack(anchor="w", pady=(4, 0))

        self._build_account_card(main_frame)
        self._build_proxy_card(main_frame)

        # Keybinds card
        card = ctk.CTkFrame(main_frame, corner_radius=12)
        card.pack(fill="x", pady=(0, 20))

        # Column headers inside card
        header_row = ctk.CTkFrame(card, fg_color="transparent")
        header_row.pack(fill="x", padx=20, pady=(16, 8))

        ctk.CTkLabel(
            header_row,
            text="ACTION",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray55"),
            width=160,
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            header_row,
            text="HOTKEY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray55"),
            width=200,
            anchor="w",
        ).pack(side="left", padx=(10, 0))

        # Separator
        sep = ctk.CTkFrame(card, height=1, fg_color=("gray75", "gray30"))
        sep.pack(fill="x", padx=16, pady=(0, 4))

        # Keybind rows
        for i, (action_id, label) in enumerate(ACTION_LABELS.items()):
            current_hotkey = self.config["hotkeys"].get(
                action_id, DEFAULT_HOTKEYS.get(action_id, "")
            )
            var = ctk.StringVar(master=self.root, value=current_hotkey)
            self.hotkey_vars[action_id] = var

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=5)

            ctk.CTkLabel(
                row,
                text=label,
                font=ctk.CTkFont(size=14),
                width=160,
                anchor="w",
            ).pack(side="left")

            entry = ctk.CTkEntry(
                row,
                textvariable=var,
                width=200,
                height=34,
                state="disabled",
                font=ctk.CTkFont(size=13, family="Consolas"),
                corner_radius=8,
            )
            entry.pack(side="left", padx=(10, 10))

            # A shortcut the .env names is owned by the .env: config.save_config writes the DEFAULT
            # for it, so capturing a new one here would show it in the box and lose it on Save.
            from_env = action_id in env_config().get("hotkeys", {})
            ctk.CTkButton(
                row,
                text=".env" if from_env else "Change",
                width=80,
                height=34,
                corner_radius=8,
                fg_color=("gray70", "gray30"),
                hover_color=("gray60", "gray40"),
                text_color=("gray10", "gray90"),
                font=ctk.CTkFont(size=13),
                state="disabled" if from_env else "normal",
                command=lambda aid=action_id: self._start_capture(aid),
            ).pack(side="left")

        # Bottom padding inside card
        ctk.CTkFrame(card, height=12, fg_color="transparent").pack()

        # Button bar — parented to the pinned strip above, not to the scrolling content.
        btn_frame = ctk.CTkFrame(self.button_area, fg_color="transparent")
        btn_frame.pack(fill="x")

        ctk.CTkButton(
            btn_frame,
            text="Reset Defaults",
            width=130,
            height=38,
            corner_radius=8,
            fg_color="transparent",
            hover_color=("gray80", "gray25"),
            text_color=("gray30", "gray70"),
            border_width=1,
            border_color=("gray60", "gray40"),
            font=ctk.CTkFont(size=13),
            command=self._reset_defaults,
        ).pack(side="left")

        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=90,
            height=38,
            corner_radius=8,
            fg_color="transparent",
            hover_color=("gray80", "gray25"),
            text_color=("gray30", "gray70"),
            border_width=1,
            border_color=("gray60", "gray40"),
            font=ctk.CTkFont(size=13),
            command=self._on_close,
        ).pack(side="right")

        ctk.CTkButton(
            btn_frame,
            text="Save",
            width=90,
            height=38,
            corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save,
        ).pack(side="right", padx=(0, 10))

    def _build_logs_tab(self, parent):
        """Live log, in memory, gone when the app exits — see log.py for why there is no file.

        The window POLLS `log.since()` on a Tk timer rather than being called back when a line is
        written. Lines arrive from the keyboard thread and from whatever thread an HTTP call is on,
        and Tk may only be touched from the thread that owns the widget, so a callback straight into
        the textbox is a crash waiting for the right timing. A 400 ms poll is invisible to a reader
        and turns the wholeproblem into one that cannot happen.
        """
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(bar, text="Live log", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkLabel(bar, text="in memory only — not written to disk, and cleared when the app exits",
                     font=ctk.CTkFont(size=11),
                     text_color=("gray50", "gray60")).pack(side="left", padx=(10, 0))

        self.log_follow = ctk.StringVar(master=self.root, value="on")
        ctk.CTkCheckBox(bar, text="Follow", variable=self.log_follow, onvalue="on", offvalue="off",
                        font=ctk.CTkFont(size=12), checkbox_width=18,
                        checkbox_height=18).pack(side="right")
        ctk.CTkButton(bar, text="Copy", width=60, height=28, corner_radius=8,
                      font=ctk.CTkFont(size=12), command=self._copy_logs).pack(side="right", padx=6)
        ctk.CTkButton(bar, text="Clear", width=60, height=28, corner_radius=8, fg_color="transparent",
                      border_width=1, border_color=("gray60", "gray40"),
                      text_color=("gray30", "gray70"), font=ctk.CTkFont(size=12),
                      command=self._clear_logs).pack(side="right")

        self.log_box = ctk.CTkTextbox(parent, wrap="none", font=ctk.CTkFont(size=12, family="Consolas"),
                                      corner_radius=8)
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_box.configure(state="disabled")
        self._log_cursor = 0
        self._pump_logs()

    def _pump_logs(self):
        """Append whatever is new, then re-arm.

        The re-arm is in `finally` on purpose. With it inside the `try`, ANY hiccup — a widget busy
        mid tab-switch, one bad line — stopped the timer for good, and the symptom is a Logs page
        that silently freezes with no error anywhere. The only thing that should end the loop is the
        window going away, which is what TclError means here.
        """
        try:
            lines, self._log_cursor = log.since(self._log_cursor)
            if lines:
                self.log_box.configure(state="normal")
                self.log_box.insert("end", "\n".join(lines) + "\n")
                self.log_box.configure(state="disabled")
                if self.log_follow.get() == "on":
                    self.log_box.see("end")
        except tkinter.TclError:
            return                       # window destroyed: stop, and do not re-arm
        except Exception:                # noqa: BLE001 — never let the log page kill the settings window
            pass
        finally:
            try:
                self.root.after(400, self._pump_logs)
            except tkinter.TclError:
                pass

    def _clear_logs(self):
        log.clear()
        self._log_cursor = log.since(self._log_cursor)[1]
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _copy_logs(self):
        """The whole buffer onto the clipboard — the point of a log page is handing it to someone."""
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_box.get("1.0", "end"))

    def _build_account_card(self, parent):
        """Which service the hotkeys drive, and the Cadence sign-in that goes with it.

        The mode is applied on Save with everything else; signing in is its own button because it opens
        a window and talks to the network — that shouldn't be tangled up with saving hotkeys.
        """
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.pack(fill="x", pady=(0, 16))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(row, text="Controls", font=ctk.CTkFont(size=14), width=160,
                     anchor="w").pack(side="left")
        self.mode_var = ctk.StringVar(master=self.root, value=self.config.get("mode", "cadence"))
        mode_menu = ctk.CTkOptionMenu(
            row, values=list(MODES), variable=self.mode_var, width=200, height=34,
            corner_radius=8, font=ctk.CTkFont(size=13), command=lambda _v: self._refresh_account(),
        )
        mode_menu.pack(side="left", padx=(10, 10))
        # A .env that pins MODE has already answered this. load_config applies it AFTER the saved
        # config, so leaving the menu live would let someone pick a mode, press Save, and find the app
        # still in the other one — the same accept-and-silently-drop the CLI refuses outright.
        self.mode_from_env = "mode" in env_config()
        if self.mode_from_env:
            mode_menu.configure(state="disabled")
        self.account_btn = ctk.CTkButton(row, text="Sign in…", width=80, height=34, corner_radius=8,
                                         font=ctk.CTkFont(size=13), command=self._cadence_sign_in)
        self.account_btn.pack(side="left")
        # Same gear as the sign-in screen. Without it, changing a rotated service token would mean
        # signing out just to get the login window back.
        self.cf_btn = ctk.CTkButton(row, text="⚙", width=36, height=34, corner_radius=8,
                                    fg_color="transparent", hover_color=("gray80", "gray25"),
                                    text_color=("gray30", "gray75"), font=ctk.CTkFont(size=16),
                                    command=self._cloudflare)
        self.cf_btn.pack(side="left", padx=(8, 0))

        self.account_hint = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=12),
                                         text_color=("gray50", "gray60"), wraplength=440, justify="left")
        self.account_hint.pack(anchor="w", padx=20, pady=(0, 14))
        self._refresh_account()

    def _build_proxy_card(self, parent):
        """Three optional boxes for a corporate proxy. Empty means "connect directly", which is what
        almost every machine wants — so this card says so rather than looking like something unset.

        The symptom of a MISSING proxy on a network that mandates one is "cannot resolve the host",
        which reads as broken DNS and sends people looking in the wrong place entirely; the hint under
        the boxes is there to shorten that.
        """
        owned = env_config()
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(card, text="Corporate proxy — optional",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=20, pady=(14, 2))
        ctk.CTkLabel(card, text="Leave empty to connect directly. Fill this in only if your network "
                                "forces traffic through a proxy —\nthe sign it does is “cannot "
                                "resolve the host”, which looks like a DNS fault but is not one.",
                     font=ctk.CTkFont(size=11), justify="left",
                     text_color=("gray50", "gray60")).pack(anchor="w", padx=20, pady=(0, 8))

        self.proxy_vars = {}
        for field, label, secret in (("proxy_url", "Proxy address", False),
                                     ("proxy_user", "Username", False),
                                     ("proxy_password", "Password", True)):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=3)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=13), width=110,
                         anchor="w").pack(side="left")
            var = ctk.StringVar(master=self.root, value=self.config.get(field, ""))
            self.proxy_vars[field] = var
            entry = ctk.CTkEntry(row, textvariable=var, width=300, height=32, corner_radius=8,
                                 font=ctk.CTkFont(size=13),
                                 placeholder_text="proxy.company.com:8080" if field == "proxy_url" else "",
                                 show="•" if secret else "")
            entry.pack(side="left", padx=(10, 0))
            # Same rule as everywhere else: a field the .env owns is shown, not editable, because
            # save_config would drop whatever was typed here.
            if field in owned:
                entry.configure(state="disabled")
                ctk.CTkLabel(row, text=".env", font=ctk.CTkFont(size=11),
                             text_color=("gray50", "gray60")).pack(side="left", padx=(8, 0))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(6, 14))
        self.proxy_auth_var = ctk.StringVar(
            master=self.root,
            value="on" if (self.config.get("proxy_auth") or "").strip() else "off")
        check = ctk.CTkCheckBox(
            row, text="Sign in to the proxy as my Windows user (NTLM / Kerberos)",
            variable=self.proxy_auth_var, onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18)
        check.pack(side="left")
        if "proxy_auth" in owned:
            check.configure(state="disabled")

    def _refresh_account(self):
        """Say what the selected mode needs and whether it has it. Local checks only (a file exists or
        it doesn't) so opening Settings never blocks on a request to a sleeping server."""
        if self.mode_var.get() == "cadence":
            signed_in = bool(self.config.get("cadence_session"))
            self.account_btn.configure(text="Sign out" if signed_in else "Sign in…", state="normal")
            self.cf_btn.pack(side="left", padx=(8, 0))   # Cloudflare token is a Cadence-only concern
            has_token = bool(self.config.get("cf_access_client_id"))
            self.account_hint.configure(text=(
                "Cadence: hotkeys are sent to your Cadence account and performed by the open Cadence "
                "tab in your browser — keep one open. " +
                ("Signed in. " if signed_in else "Not signed in yet. ") +
                ("⚙ Cloudflare Access token set." if has_token else "⚙ No Cloudflare Access token.")
            ) + self._env_suffix())
        else:
            has_env = bool(self.config.get("spotify_client_id"))
            self.account_btn.configure(text="Credentials…", state="normal")
            self.cf_btn.pack_forget()
            self.account_hint.configure(text=(
                "Spotify: hotkeys drive this machine's Spotify Connect device, using a Spotify app's "
                "Client ID and Secret. " +
                ("Credentials saved." if has_env else "No credentials yet — click Credentials.")
            ) + self._env_suffix())

    def _env_suffix(self):
        """Say so when a .env is driving this, instead of leaving a greyed-out control unexplained."""
        if not self.mode_from_env:
            return ""
        return f"\n\nMode is set by MODE in {env_path()} — the selector above follows that file."

    def _cadence_sign_in(self):
        """The account button: sign in / sign out for Cadence, or the credentials form for Spotify.
        login_ui is imported here rather than at module load so the settings window keeps no
        import-time dependency on the network client."""
        from music_agent.ui import login

        if self.mode_var.get() == "spotify":
            self._with_hidden_window(lambda: login.spotify_setup(self.config))
            return
        if self.account_btn.cget("text") == "Sign out":
            client_from_config(self.config).forget()
            self._refresh_account()
            if self.on_save_callback:
                # Sign out has to reach the RUNNING controller too. Clearing only the file left the
                # live client holding the cookie in its jar: hotkeys kept working after "Sign out",
                # and the next one wrote a fresh session straight back into the config.
                self.on_save_callback()
            return
        self._with_hidden_window(lambda: login.sign_in(self.config))

    def _cloudflare(self):
        """Cloudflare Access service token — a dialog over this window, not a second root."""
        from music_agent.ui import login

        login.cloudflare_dialog(self.root, self.config)
        self._refresh_account()

    def _with_hidden_window(self, action):
        """Run a setup window with this one out of the way — each is its own CTk root, and stacking two
        of them makes both misbehave."""
        self.root.withdraw()
        try:
            action()
        finally:
            self.root.deiconify()
            self.config = load_config()   # the setup windows save settings of their own — pick them up
            self._refresh_account()

    def _start_capture(self, action_id):
        """Open a modal overlay that captures a hotkey press."""
        if self._capturing:
            return
        self._capturing = True

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Press Hotkey")
        dialog.geometry("340x140")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.attributes("-topmost", True)
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._cancel_capture(dialog))

        icon_path = find_icon()
        if icon_path:
            try:
                dialog.iconbitmap(icon_path)
            except Exception:
                pass

        # Center dialog over parent
        dialog.update_idletasks()
        px = self.root.winfo_x() + (self.root.winfo_width() // 2) - 170
        py = self.root.winfo_y() + (self.root.winfo_height() // 2) - 70
        dialog.geometry(f"+{px}+{py}")

        ctk.CTkLabel(
            dialog,
            text="Press your key combination...",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(expand=True, pady=(20, 5))

        current = self.hotkey_vars[action_id].get()
        ctk.CTkLabel(
            dialog,
            text=f"Current: {current}",
            font=ctk.CTkFont(size=12),
            text_color=("gray50", "gray55"),
        ).pack(pady=(0, 5))

        ctk.CTkButton(
            dialog,
            text="Cancel",
            width=80,
            height=32,
            corner_radius=8,
            fg_color="transparent",
            hover_color=("gray80", "gray25"),
            text_color=("gray30", "gray70"),
            border_width=1,
            border_color=("gray60", "gray40"),
            command=lambda: self._cancel_capture(dialog),
        ).pack(pady=(0, 15))

        self._capture_cancelled = False
        self._capture_hook = None

        def on_key(event):
            name = event.name.lower()
            # Ignore bare modifier presses — wait for a real key
            if name in _MODIFIERS:
                return
            if self._capture_cancelled:
                return
            # Build combo from currently held modifiers
            parts = []
            for mod in ("ctrl", "alt", "shift", "windows"):
                if keyboard.is_pressed(mod):
                    parts.append(mod)
            parts.append(name)
            hotkey = "+".join(parts)
            try:
                self.root.after(0, lambda: self._apply_capture(action_id, hotkey, dialog))
            except Exception:
                pass

        self._capture_hook = keyboard.on_press(on_key)

    def _apply_capture(self, action_id, hotkey, dialog):
        """Apply the captured hotkey and close the dialog."""
        if self._capture_cancelled:
            return
        self._unhook_capture()
        self.hotkey_vars[action_id].set(hotkey)
        self._capturing = False
        try:
            dialog.destroy()
        except Exception:
            pass

    def _unhook_capture(self):
        """Remove the capture keyboard hook if active."""
        if self._capture_hook is not None:
            keyboard.unhook(self._capture_hook)
            self._capture_hook = None

    def _cancel_capture(self, dialog):
        self._capture_cancelled = True
        self._unhook_capture()
        self._capturing = False
        try:
            dialog.destroy()
        except Exception:
            pass

    def _save(self):
        """Validate and save hotkeys, then trigger reload callback."""
        new_hotkeys = {aid: var.get() for aid, var in self.hotkey_vars.items()}

        # Check for duplicates
        seen = {}
        for aid, hk in new_hotkeys.items():
            if hk in seen:
                dup_label = ACTION_LABELS[seen[hk]]
                cur_label = ACTION_LABELS[aid]
                error_dialog = ctk.CTkToplevel(self.root)
                error_dialog.title("Duplicate Hotkey")
                error_dialog.geometry("380x140")
                error_dialog.resizable(False, False)
                error_dialog.transient(self.root)
                error_dialog.grab_set()
                error_dialog.attributes("-topmost", True)

                error_dialog.update_idletasks()
                px = self.root.winfo_x() + (self.root.winfo_width() // 2) - 190
                py = self.root.winfo_y() + (self.root.winfo_height() // 2) - 70
                error_dialog.geometry(f"+{px}+{py}")

                ctk.CTkLabel(
                    error_dialog,
                    text=f'"{hk}" is used by both\n"{dup_label}" and "{cur_label}"',
                    font=ctk.CTkFont(size=13),
                    wraplength=340,
                ).pack(expand=True, pady=(20, 10))

                ctk.CTkButton(
                    error_dialog,
                    text="OK",
                    width=80,
                    height=32,
                    corner_radius=8,
                    command=error_dialog.destroy,
                ).pack(pady=(0, 15))
                return
            seen[hk] = aid

        self.config["hotkeys"] = new_hotkeys
        self.config["mode"] = self.mode_var.get()
        for field, var in self.proxy_vars.items():
            self.config[field] = var.get().strip()
        self.config["proxy_auth"] = "current-user" if self.proxy_auth_var.get() == "on" else ""
        try:
            save_config(self.config)
            # Apply immediately rather than at the next launch: someone who just typed a proxy in is
            # about to press Sign in, and doing that through the old (absent) proxy would tell them
            # the address is wrong when it is the timing that is.
            config_module.apply_proxy(load_config())
        except OSError:
            error_dialog = ctk.CTkToplevel(self.root)
            error_dialog.title("Save Error")
            error_dialog.geometry("340x120")
            error_dialog.resizable(False, False)
            error_dialog.transient(self.root)
            error_dialog.grab_set()
            ctk.CTkLabel(
                error_dialog,
                text="Failed to save configuration.\nCheck disk space and permissions.",
                font=ctk.CTkFont(size=13),
            ).pack(expand=True, pady=(20, 10))
            ctk.CTkButton(
                error_dialog, text="OK", width=80, height=32,
                corner_radius=8, command=error_dialog.destroy,
            ).pack(pady=(0, 15))
            return
        callback = self.on_save_callback
        self._on_close()
        if callback:
            callback()

    def _reset_defaults(self):
        """Reset all hotkeys to defaults."""
        for action_id, default in DEFAULT_HOTKEYS.items():
            if action_id in self.hotkey_vars:
                self.hotkey_vars[action_id].set(default)

    def _on_close(self):
        global _window_open
        self._capture_cancelled = True
        self._unhook_capture()
        with _window_lock:
            _window_open = False
        self.root.destroy()


def open_settings(on_save_callback=None):
    """Open settings window in a new thread. Safe to call from any thread."""
    def run():
        SettingsWindow(on_save_callback=on_save_callback)
    threading.Thread(target=run, daemon=True).start()
