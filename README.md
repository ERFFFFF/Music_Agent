
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

## ⌨️ No-GUI mode (`music_agent/cli.py`)

Everything the Settings window does, from a terminal — same `cadence_config.txt`, same two modes, same
actions. There is no separate configuration to keep in sync: the tray app and the CLI read and write
the one file.

**Start it with no arguments.** It stays running and listens for your hotkeys until Ctrl+C — that is
what this program *is*, and nothing needs installing first:

```powershell
python -m music_agent
```

```text
  ctrl+alt+up            play_pause
  ctrl+alt+right         next_track
  ctrl+alt+left          previous_track
  ctrl+alt+l             like_unlike
  ctrl+alt+c             show_current

Music Agent running in cadence mode. Ctrl+C to stop.
Every press prints a line below.

02:04:17  play_pause      ok
02:04:20  next_track      ok
02:04:22  previous_track  ok
02:04:25  like_unlike     Like toggled
02:04:28  show_current    Memories - DMNDS, LIUUC, Tny  (paused)
```

**Every press prints a line**, including the actions that have nothing to say — `ok` is the point, so
a working `next_track` never looks the same as a key that didn't fire. A press that failed prints
`FAILED` and the reason, on stderr:

```text
02:11:04  next_track      FAILED  No Cadence tab is open - open Cadence in your browser to control playback.
```

Leave that window open and the keys work everywhere, in any application. Everything below is *extra* —
one-shot commands for scripting, or for finding out why something isn't working. Each does one thing
and exits, so they are **not** a substitute for leaving the agent running:

```powershell
python -m music_agent status      # what's configured, and where each value came from
python -m music_agent now         # what's playing
python -m music_agent play        # ...and pause / toggle / next / prev / like
python -m music_agent devices     # Spotify Connect devices this account can see
python -m music_agent login       # sign in — normally automatic, from the .env
python -m music_agent hotkeys                          # list them, and where each came from
python -m music_agent hotkeys play_pause ctrl+alt+p    # rebind one
python -m music_agent hotkeys reset
python -m music_agent run         # the agent again, spelled out — same as no arguments
python -m music_agent -v          # ...and -v works on anything, agent included
```

The working directory doesn't matter — `python D:\path\to\music_agent/cli.py` finds its `.env` and its
config next to the script, not next to you.

**`play` and `pause` mean what they say**, in both modes: each reads the current state first, so
running one twice is a no-op rather than the opposite action. `toggle` is the flip, and it's what the
hotkey binds. **Exit code 1 on failure**, so `music_agent/cli.py play || alert-me` works — the message
goes to stderr, everything else to stdout.

### Credentials live in `.env`, and only there

Copy [`.env.example`](.env.example) to `.env` beside the app and fill in what your mode needs — server
address, Cadence account, Cloudflare Access service token, Spotify app keys. The app reads it every run
and **never writes to it**; nothing from it is copied into `cadence_config.txt`, so rotating a key means
editing one file. With `USERNAME` and `PASSWORD` set, the CLI signs itself in on first use and no
command ever prompts.

**The mode and all five shortcuts live there too.** These are every hotkey the app has — `ACTIONS` in
`config.py` is the single source, and the `.env` key is always `HOTKEY_` + the action id, so a new
action is configurable the moment it exists. Set any subset; the rest keep what they had.

```ini
MODE=spotify                            # cadence | spotify

HOTKEY_PLAY_PAUSE=ctrl+alt+up
HOTKEY_NEXT_TRACK=ctrl+alt+right
HOTKEY_PREVIOUS_TRACK=ctrl+alt+left
HOTKEY_LIKE_UNLIKE=ctrl+alt+l
HOTKEY_SHOW_CURRENT=ctrl+alt+c
```

### Seeing what it's doing

```powershell
python -m music_agent -d          # the agent, streaming every HTTP call, status and timing
python -m music_agent -d now      # ...or on any one-shot command
```

The tray app shows the same log on **Settings → Logs**, live, with the history from before you
opened it, plus Copy and Clear. It is held **in memory only** — never written to disk, and gone when
the app exits — because a log file naming your server and your account is a support burden and a
privacy question nobody asked for.

### Behind a corporate proxy

```ini
PROXY=proxy.corp.example:8080            # address; host:port is enough
PROXY_USER=alice                         # optional
PROXY_PASSWORD=s3cr3t                    # optional
NO_PROXY=localhost,127.0.0.1             # optional, comma-separated
```

User and password are separate keys rather than `user:pass@host` so a password containing `@`, `:`
or `/` cannot split the URL — the app percent-encodes them for you. The same three boxes are in the
tray app under **Settings → Corporate proxy**, along with the Windows-login checkbox; all optional,
and empty means connect directly.

**On a network that mandates a proxy, the failure does not look like a proxy problem.** You get
`Can't reach … : [Errno 11001] getaddrinfo failed` — a *DNS* error — because the client is not
supposed to resolve external names there at all; the proxy does it. So "cannot resolve the host"
usually means "no proxy configured", not "DNS is broken".

Set `HTTP_PROXY` / `HTTPS_PROXY` separately if they must differ. Anything set here beats a variable
already in your shell. `status` shows which proxy is in effect and where it came from, with any
password printed as `***`.

Windows proxy *settings* are picked up automatically, so often you need nothing. A **PAC file**
(Automatic configuration script) is not read by the default transport — see below.

#### Proxy asks for your Windows login (NTLM / Kerberos)

```ini
PROXY_AUTH=current-user
```

Most corporate proxies answer 407 with `NTLM` or `Negotiate` rather than `Basic`. `urllib` speaks
only Basic — measured against proxies demanding each, it never attempts them — so this switches the
transport to **Windows' own HTTP stack** (`net/winhttp.py`, ctypes, no package), which does the SSPI
handshake as the logged-in user and reads PAC files too. Leave it empty and nothing changes: the
default transport stays in use.

> **Windows 10 cannot do TLS 1.3.** Its TLS stack stops at 1.2, so if your server *requires* TLS 1.3
> — Cloudflare → SSL/TLS → Edge Certificates → Minimum TLS Version — then nothing built on the
> Windows stack can reach it, including `curl` and PowerShell. This is measured, not theoretical: it
> is exactly what this project's own server does. Either set that minimum to 1.2, or leave
> `PROXY_AUTH` empty. Windows 11 is unaffected. The app says so in as many words rather than
> reporting a TLS error code.

**One rule covers all of it: what the `.env` supplies, the `.env` keeps.** The config file stores that
field's *default* instead, so there is never a second copy to rotate, and deleting a line returns the
setting to its default rather than resurrecting whatever was last saved under it.

Everything that could edit such a value refuses instead of pretending: `set` and `hotkeys` exit 1 with
the file to edit, Settings greys out the mode selector and shows `.env` on the affected hotkey rows,
and the first-run chooser is skipped entirely when `MODE` is pinned. A control that accepts a value and
silently drops it is worse than one that isn't there.

### The other file: `cadence_config.txt`

It sits next to the app and holds **everything the `.env` doesn't** — the things the app *earns* or you
choose while using it, not the things you configure:

| Field | What it is | Where it comes from |
|---|---|---|
| `cadence_session` | The signed Cadence session cookie | Earned by signing in. Slides on every command, so an agent in use never logs in again |
| `spotify_refresh_token` | The OAuth token from Spotify's browser consent | Earned once at consent. Spotify may hand back a new one on any refresh; it is re-saved when it does |
| `mode` | `cadence` or `spotify` | `set mode`, Settings, or `MODE` in the `.env` |
| `hotkeys` | Only the ones the `.env` does **not** name | `hotkeys <action> <combo>`, or Settings |

The name is historical — it predates Spotify mode living in the same file. It is **not** a second copy
of your credentials: everything the `.env` supplies is blanked on every write, so there is exactly one
place to rotate a key. Every credential still in it is encrypted at rest with Windows **DPAPI**, keyed
to your Windows account — copy it to another PC or user and those fields simply read as empty and you
sign in once there. `mode` and `hotkeys` stay readable on purpose so a moved copy still looks sane.

It is gitignored, and **safe to delete** — see below.

### Stopping and restarting

Every command is already a fresh process; there is no daemon holding state between them. Restarting
changes nothing, because everything needed to resume lives on disk. Measured, not assumed:

| You do this | What happens |
|---|---|
| Stop and restart `run` | Rebinds all five hotkeys and carries on. No sign-in. |
| Run any verb, any number of times | Each is a cold start that reuses the saved session |
| Delete `cadence_config.txt` | **Still works.** Cadence signs back in from `USERNAME`/`PASSWORD`; Spotify re-adopts its refresh token. Only your mode and any config-file-owned hotkey go back to default |
| Delete `.env` | **Cadence mode breaks** — the server address lived there. The session cookie survives but has nothing to point at. Restore the file, or run `setup` to store the settings in `cadence_config.txt` instead |
| Reboot / log out | No effect. DPAPI decrypts for the same Windows account |
| Copy the folder to another PC | Sign in once there — DPAPI won't decrypt someone else's blobs. Your `.env` still works, so that sign-in is automatic |

The one thing that *can* cost you a browser click is deleting `cadence_config.txt` in Spotify mode,
because the refresh token is earned, not configured — it cannot be re-derived from the `.env`. (On this
machine it is also still recoverable from the old spotipy cache in `%LOCALAPPDATA%`.)

### Dependencies: none

```powershell
pip install -r requirements-cli.txt    # empty — the CLI runs on the standard library alone
pip install -r requirements-gui.txt    # customtkinter + pystray + Pillow + keyboard, for the tray app
```

`music_agent/cli.py` reaches no third-party package at all. The two it used to need were replaced with
the thing they were wrapping:

| Was | Now | Why |
|-----|-----|-----|
| `requests` | [`net/httpmin.py`](src/music_agent/net/httpmin.py) | ~140 lines of `urllib.request` in a `requests` shape. Both backends make small JSON calls and nothing else. |
| `keyboard` | [`win32/hotkeys.py`](src/music_agent/win32/hotkeys.py) | Win32 `RegisterHotKey` + a message loop, which *is* the OS's global-hotkey mechanism — no low-level keyboard hook, so no administrator rights. |

The tray app still needs `keyboard`, because `ui/settings.py` **records** a combination as you press
it, and that is the one thing `RegisterHotKey` cannot do.

### Building the portable exe

```powershell
.\build_portable.ps1           # -> dist\MusicAgent_portable.exe   tray app
```

**The CLI is not built into an exe, on purpose.** It has no dependencies to bundle, so freezing it
would only add a 10 MB artifact and a rebuild step between every change, to ship an interpreter you
already have. Run it live — `python -m music_agent <verb>`, edit, run again. Only the tray app
earns an exe, because it has to start from a shortcut with no console.

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
pip install -e ".[dev]"                # editable install + pytest; gives you `music-agent` too
pip install -e ".[gui]"                # ...add the tray app's toolkit if you're working on the UI
```

### 2. Configure

Copy `.env.example` to `.env` next to `ui/tray.py` and fill in your server, account and keys — that file
is the source of truth for every credential, is read on every run, and is never written to. It is
gitignored and must never be committed.

Everything else lands in **`cadence_config.txt`** beside the app: the mode, the hotkeys, the Cadence
session cookie and the Spotify refresh token. Delete it to start clean; your `.env` survives. The old
`config.json` / `cadence_session.json` are read once and migrated into it, so an existing install keeps
its settings.

You can skip the `.env` entirely and run `python -m music_agent setup` (or just launch the tray
app) to be asked instead — the answers go into `cadence_config.txt`, encrypted with DPAPI.

Self-checks:

```bash
python -m pytest   # everything below, plus the cross-module checks. Run this one.

python -m pytest tests/test_httpmin.py    # the HTTP shim, against a real loopback server
python -m pytest tests/test_config.py     # config paths, round-trip, .env precedence, corrupt-file fallback
python -m pytest tests/test_cadence.py    # cookie handling + "is this a wall or an answer?" detection
python -m pytest tests/test_hotkeys.py # combination parsing + real RegisterHotKey clash/cleanup (Windows only)
python -m music_agent now    # ...plus a live round trip
```

### 3. Build the Executable

#### Full build (first time or after code changes)

Builds the exe from scratch, creates a shortcut in the Windows Startup folder so the agent launches on login, and registers the app ID for notifications.

Run in PowerShell inside the repository folder :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, tray_entry.spec, "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Music Agent.lnk"; pyinstaller --noconsole --icon=poulet.ico --name "Music Agent" tray_entry.py; $ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Music Agent.lnk"); $sc.TargetPath = (Resolve-Path ".\dist\Music Agent\Music Agent.exe").Path; $sc.WorkingDirectory = (Resolve-Path ".\dist\Music Agent").Path; $sc.Save(); New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
```

#### Quick rebuild (using the spec file)

If you already ran the full build once and just need to rebuild after a small change, you can reuse the existing `Music Agent.spec` file. It already contains all the build settings (icon, bundled `.env`, no console, etc.) so you don't need to pass them again :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build; pyinstaller "Music Agent.spec"
```

The output is at `dist\Music Agent\Music Agent.exe`. The Startup shortcut still points to it, so no need to recreate it.

#### Build only (without startup shortcut)

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, tray_entry.spec; pyinstaller --noconsole --icon=poulet.ico --name "Music Agent" tray_entry.py; New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
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
