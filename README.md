
# Tags

![Python](https://img.shields.io/badge/python-3.10.5-blue.svg)
![PyPI version](https://badge.fury.io/py/pip.svg)

# Music Control Agent

A lightweight agent that controls your music with global keybinds — skip, pause, like, or jump back
without leaving whatever you're doing.

It drives **either** of two things, picked in Settings:

| Mode | What it controls | What it needs |
|------|------------------|---------------|
| **Cadence** (default) | Your [Cadence](https://github.com/ERFFFFF/cadence) account — the command is performed by your open Cadence browser tab | Just your Cadence login |
| **Spotify** | This machine's Spotify Connect device, via the Web API | A Spotify developer app (the Client ID + Secret, asked for on first launch) |

**Cadence mode is the portable one**: no developer app, no secrets baked in at build time, no installer —
one `MusicAgent_portable.exe` you can copy anywhere, sign in once, done. It also works on a network where
Spotify itself is blocked but Cadence (plain HTTPS) is not, which is the whole reason it exists.

## 🌟 Project Goal

Create an agent that provides seamless control over your music using keybinds.

## 🎧 Features

- **Skip to the Next Song**: Quickly move to the next track in your playlist.
- **Pause/Play**: Toggle between pausing and playing the current track.
- **Like/Unlike Songs**: Instantly like or unlike the currently playing song.
- **Previous Song**: Return to the previous track with a simple keypress.
- **Show Current Song**: A notification with what's playing right now.

*(Spotify mode picks a Connect device on its own — the active one if there is one, otherwise the first
it can see — and every command names that device explicitly, so an idle-but-running Spotify client
starts playing without a separate "wake" step.)*

## 🚦 First launch — pick your account

However you got the app (portable exe or installer), the **first launch asks how you want to control
your music**:

- **Cadence account** → a sign-in window (server, username, password).
- **Spotify app** → a form for your **Client ID + Client Secret**, saved into `cadence_config.txt` beside the app.

Both are remembered, so it only asks once. Switch later from the tray icon → **Settings** → *Controls*,
where the same two windows are one click away (*Sign in… / Sign out*, *Credentials…*).

## 🎵 Cadence mode (portable, no install)

1. Grab `MusicAgent_portable.exe` (or build it: `.\build_portable.ps1`) and put it wherever you like.
2. Run it, choose **Cadence account**, then fill in **your own server's address** (there is no default
   baked into the app) and sign in with the account you use in the web player. Cadence has no
   self-registration, so this is an account the operator made for you.
3. That's it: it goes to the system tray and the hotkeys work.

> **"Windows protected your PC"?** Click **More info → Run anyway** — once, then never again.
> Windows tags anything that arrived from the internet with a *Mark-of-the-Web*, and SmartScreen
> warns on tagged programs it doesn't recognise yet. It's about where the file came from, not about
> what's in it: an exe you build yourself with `build_portable.ps1` is never tagged and never asks.
> To clear it up front instead: right-click the exe → Properties → tick **Unblock** → OK, or
> `Unblock-File .\MusicAgent_portable.exe` in PowerShell. Both just delete that tag from that one file — no
> admin rights, nothing changed on your PC, no SmartScreen setting touched. Unblocking the `.zip`
> *before* extracting saves doing it to the exe afterwards.

Everything the app remembers lives in **one file next to it — `cadence_config.txt`**: the mode, your
Cadence URL and signed-in session, the Cloudflare token, the Spotify keys, and your shortcuts. (If the
folder is read-only — an installed copy under Program Files — it falls back to `%LOCALAPPDATA%\MusicAgent`.)

> **The credentials in it are encrypted, not plaintext.** The session token, URL and API keys are
> sealed with Windows DPAPI, which ties them to *your Windows account*: another user on the PC, or
> anyone who copies the file off it, gets unreadable blobs. Only `mode` and your hotkeys stay readable.

That protection is also why the file doesn't travel. Move the exe to **another PC or another Windows
account** and the secrets won't decrypt there, so it asks you to sign in once on the new machine —
your shortcuts and mode come across intact. Sign out in Settings clears the session either way.

Using the app keeps you signed in: each hotkey refreshes the session, so it only expires after ~7 days
of not touching it at all.

**Keep a Cadence tab open.** Cadence plays audio in the browser: the tab owns the queue and the audio
element, so a hotkey queues an intent that the tab performs within about a second. If no tab is open the
agent says so rather than silently doing nothing.

**Cloudflare Access?** If your Cadence sits behind it — mine does — a request without a service
token is answered with a `302` to the Access login page (verified), which no background app can
complete. So:

1. Cloudflare Zero Trust → **Access → Service Auth → Create Service Token**; copy the Client ID and
   Client Secret (shown once).
2. Open the Cadence application's Access policy and add a rule: *Action: Service Auth*, include
   **Service Token** → the one you just made.
3. Click the **⚙ button in the top-right corner of the sign-in window** and paste both values in.
   (Same dialog later from the tray → Settings → *Controls* → ⚙, so a rotated token doesn't mean
   signing out.)

The sign-in window says whether a token is set, and without one the app names Access as the blocker
rather than failing vaguely. A Cadence server that isn't behind Access needs none of this — leave the
fields empty.

**Waking up.** Cadence stops itself after ~30 minutes idle and takes a few seconds to come back; a
hotkey pressed in that window says "Cadence is waking up — try again in a few seconds". (If it was
asleep, no browser tab was open either, so there was nothing to control regardless.)

## ⌨️ No-GUI mode (`cli.py`)

Everything the Settings window does, from a terminal — same `cadence_config.txt`, same two modes, same
five actions. There is no separate configuration to keep in sync: the tray app and the CLI read and
write the one file.

```powershell
python cli.py setup       # interactive: pick a mode, enter a server, sign in
python cli.py status      # what's configured and what isn't
python cli.py now         # what's playing
python cli.py next        # ...and play / pause / prev / like
python cli.py hotkeys                          # list them
python cli.py hotkeys play_pause ctrl+alt+p    # rebind one
python cli.py hotkeys reset
python cli.py run         # the hotkey agent itself, headless — Ctrl+C stops it
```

`cli.py` imports **no GUI libraries at all**, so a CLI-only machine installs two packages instead of
five:

```powershell
pip install -r requirements.txt        # requests + keyboard — enough for cli.py
pip install -r requirements-gui.txt    # adds customtkinter + pystray + Pillow, for the tray app
```

### Building the portable exe

```powershell
.\build_portable.ps1           # -> dist\MusicAgent_portable.exe   tray app
.\build_portable.ps1 -Cli      # -> dist\MusicAgent_cli.exe        console app, no GUI libraries
```

## 🔗 Useful Links

- **Spotify Developer Portal**: [Spotify Dashboard](https://developer.spotify.com/dashboard/)
- **Inno Setup**: [Download](https://jrsoftware.org/isdl.php) (only needed to build the installer)

## 🚀 Installation (for users)

Download `MusicAgentSetup.exe` from the [Releases](https://github.com/ERFFFFF/Music_Agent/releases) page and run it.

The installer will:

1. Ask where to install the app
2. Optionally add the app to Windows startup
3. Create Start Menu shortcuts and an uninstaller

It no longer asks for Spotify credentials: **the app asks on first launch and supports either account**
(see [First launch](#-first-launch--pick-your-account)). That's what lets an installed copy use Cadence
too — the old installer could only set up Spotify.

In Spotify mode, a browser window opens once for Spotify authorization after you enter your
credentials. After that, the app runs silently in the background.

### How to get Spotify credentials

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/)
2. Create a new app
3. In your app's **Settings**, copy the **Client ID** and **Client Secret**
4. Add `http://127.0.0.1:8888/callback` as a **Redirect URI** in the app settings

The device is auto-discovered at startup — the agent picks the first available Spotify Connect device. If no device is found, it retries every 5 minutes until one appears.

## 🔧 Development Setup (for contributors)

### 1. Install Required Packages

```bash
pip install -r requirements.txt
```

### 2. Configure

Nothing to create by hand: run the app and it asks (Cadence sign-in, or Spotify Client ID + Secret),
then writes **`cadence_config.txt`** next to `main.py`. That one file holds the mode, the Cadence URL
and session, the Cloudflare token, the Spotify keys and the hotkeys — delete it to start clean.

A legacy `.env` (or the old `config.json` / `cadence_session.json`) is read once and migrated into it,
so an existing install keeps its settings. `.env.example` documents that legacy format; a real
`.env` is gitignored and must never be committed.

Self-checks, runnable on any OS:

```bash
python config.py     # config paths, round-trip, migration, corrupt-file fallback
python cadence.py    # cookie handling + "is this a wall or an answer?" detection
python cadence.py https://your-cadence user password    # ...plus a live round trip
```

### 3. Build the Executable

#### Full build (first time or after code changes)

Builds the exe from scratch, creates a shortcut in the Windows Startup folder so the agent launches on login, and registers the app ID for notifications.

Run in PowerShell inside the repository folder :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, main.spec, "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Music Agent.lnk"; pyinstaller --noconsole --icon=poulet.ico --name "Music Agent" main.py; $ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Music Agent.lnk"); $sc.TargetPath = (Resolve-Path ".\dist\Music Agent\Music Agent.exe").Path; $sc.WorkingDirectory = (Resolve-Path ".\dist\Music Agent").Path; $sc.Save(); New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
```

#### Quick rebuild (using the spec file)

If you already ran the full build once and just need to rebuild after a small change, you can reuse the existing `Music Agent.spec` file. It already contains all the build settings (icon, bundled `.env`, no console, etc.) so you don't need to pass them again :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build; pyinstaller "Music Agent.spec"
```

The output is at `dist\Music Agent\Music Agent.exe`. The Startup shortcut still points to it, so no need to recreate it.

#### Build only (without startup shortcut)

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, main.spec; pyinstaller --noconsole --icon=poulet.ico --name "Music Agent" main.py; New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
```

### 4. Build the Installer

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php) to be installed.

#### Using the build script (recommended)

Builds the exe with PyInstaller then compiles the installer in one step:

```powershell
.\installer\build_installer.ps1
```

Output: `installer_output\MusicAgentSetup.exe`

#### Manual steps

```powershell
# Step 1: Build with PyInstaller
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build
pyinstaller "Music Agent.spec"

# Step 2: Compile installer (adjust path to your Inno Setup install)
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss
```

## 🛠️ Useful Commands

- **Terminate All Running Instances of the Agent:**

   If you need to close all running instances of the Spotify Agent, use the following command in PowerShell:

   ```powershell
   Stop-Process -Name "Music Agent" -Force
   ```
