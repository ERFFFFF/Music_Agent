import os
import sys
import threading
import logging
import customtkinter as ctk
import keyboard

from config import load_config, save_config, DEFAULT_HOTKEYS

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


def _find_icon():
    """Locate poulet.ico using the same pattern as main.py's find_env()."""
    exe_dir = os.path.dirname(sys.executable)
    beside_exe = os.path.join(exe_dir, "poulet.ico")
    if os.path.isfile(beside_exe):
        return beside_exe
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.abspath(".")
    bundled = os.path.join(base, "poulet.ico")
    if os.path.isfile(bundled):
        return bundled
    return None


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
        icon_path = _find_icon()
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
        # Main container
        main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        main_frame.pack(padx=30, pady=25, fill="both", expand=True)

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
            var = ctk.StringVar(value=current_hotkey)
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

            ctk.CTkButton(
                row,
                text="Change",
                width=80,
                height=34,
                corner_radius=8,
                fg_color=("gray70", "gray30"),
                hover_color=("gray60", "gray40"),
                text_color=("gray10", "gray90"),
                font=ctk.CTkFont(size=13),
                command=lambda aid=action_id: self._start_capture(aid),
            ).pack(side="left")

        # Bottom padding inside card
        ctk.CTkFrame(card, height=12, fg_color="transparent").pack()

        # Button bar
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
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

        icon_path = _find_icon()
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
        try:
            save_config(self.config)
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
