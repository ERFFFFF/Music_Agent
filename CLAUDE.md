# Music Agent

Windows-only Spotify playback controller using global hotkeys. Runs as a background startup agent (PyInstaller exe). Distributed via Inno Setup installer.

## Tech Stack

- **Python 3.10.5** on Windows 10+
- **spotipy** — Spotify Web API client with OAuth2
- **keyboard** — global hotkey listener (requires admin on some systems)
- **appdirs** — OS-specific persistent storage paths
- **ratelimit** — API call throttling (1 call/sec)
- **python-dotenv** — loads config from `.env` file
- **ctypes** — Windows shell notifications + single-instance mutex
- **PyInstaller** — builds `--onedir` exe (single process)
- **Inno Setup** — creates the Windows installer

## Project Structure

```
main.py                 # Main app: OAuth, hotkeys, playback control, notifications
discover_device_id.py   # Utility to list Spotify Connect devices
.env                    # Spotify credentials (encrypted via git-crypt)
.env.template           # Template for others to create their own .env
.gitattributes          # git-crypt encryption rules
requirements.txt        # Python dependencies
poulet.ico              # App icon for exe and installer
Music Agent.spec        # PyInstaller build spec (gitignored)
installer/              # Installer files (Inno Setup script, build script, info text)
```

## Key Architecture Decisions

- **Config via `.env`** loaded with `dotenv_values()`. The `find_env()` function checks next to the exe first (installer scenario), then falls back to `resource_path()` / `sys._MEIPASS` (dev/bundled scenario).
- **Installer** creates the `.env` at install time with user-provided Spotify credentials. No `.env` needs to be bundled in the PyInstaller output for installer builds.
- **git-crypt** encrypts `.env` in the repo so secrets are safe. `.env.template` is plaintext for others.
- **OAuth token cache** is stored in `appdirs.user_data_dir()` (`AppData/Local/MusicAgent/...`), NOT relative to the exe.
- **Device auto-discovery** at startup — picks the first available Spotify Connect device. Retries every 5 minutes if none found.
- **Single-instance** enforcement uses a Windows named mutex (`Global\MusicAgentMutex`).
- **Notifications** use raw Win32 `Shell_NotifyIconW` via ctypes.
- **Rate limiting** wraps all Spotify API calls through `rate_limited_spotify_call()` at 1 call/sec.
- **redirect_uri** must be `http://127.0.0.1:8888/callback` (not localhost). Must match the Spotify Developer Dashboard setting exactly.

## Hotkeys

| Shortcut | Action |
|----------|--------|
| Ctrl+Alt+Up | Play/Pause |
| Ctrl+Alt+Right | Next track |
| Ctrl+Alt+Left | Previous track |
| Ctrl+Alt+L | Like/unlike current song |
| Ctrl+Alt+C | Show current song notification |
| Ctrl+Alt+W | Wake device (requires Spotify client running on target) |

## Building

### Exe only (dev)

```powershell
pyinstaller "Music Agent.spec"
```

Output: `dist\Music Agent\Music Agent.exe`

### Full installer

```powershell
.\installer\build_installer.ps1
```

Output: `installer_output\MusicAgentSetup.exe`

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php).

## First-Run Auth

The first launch opens a browser for Spotify OAuth. After that, the token auto-refreshes silently forever. The `.cache` file persists in AppData across restarts.

## Common Pitfalls

- Never store the `.cache` token file relative to the `.env` path — it ends up in PyInstaller's temp dir and gets deleted.
- `sp.transfer_playback()` takes `device_id` (string), not `device_ids` (list).
- `.env` is encrypted by git-crypt — run `git-crypt unlock` after clone if you have the key.
- Environment variable keys are `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`.
- The installer writes `.env` next to the exe. `find_env()` checks there first before `_MEIPASS`.
