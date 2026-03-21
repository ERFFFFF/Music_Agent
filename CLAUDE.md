# Music Agent

Windows-only Spotify playback controller using global hotkeys. Runs as a background startup agent (PyInstaller exe).

## Tech Stack

- **Python 3.10.5** on Windows 10+
- **spotipy** — Spotify Web API client with OAuth2
- **keyboard** — global hotkey listener (requires admin on some systems)
- **appdirs** — OS-specific persistent storage paths
- **ratelimit** — API call throttling (1 call/sec)
- **python-dotenv** — loads config from `.env` file
- **ctypes** — Windows shell notifications + single-instance mutex
- **PyInstaller** — builds single-file `.exe`

## Project Structure

```
main.py                 # Main app: OAuth, hotkeys, playback control, notifications
discover_device_id.py   # Utility to list Spotify Connect devices
.env                    # Spotify credentials + device_id (encrypted via git-crypt)
.env.template           # Template for others to create their own .env
.gitattributes          # git-crypt encryption rules
requirements.txt        # Python dependencies
poulet.ico              # App icon for exe
Music Agent.spec        # PyInstaller build spec
```

## Key Architecture Decisions

- **Config via `.env`** loaded with `dotenv_values()`. The `.env` is bundled inside the exe via PyInstaller `--add-data` and located at runtime via `resource_path()`.
- **git-crypt** encrypts `.env` in the repo so secrets are safe. `.env.template` is plaintext for others.
- **OAuth token cache** is stored in `appdirs.user_data_dir()` (`AppData/Local/MusicAgent/...`), NOT relative to the exe. This is critical — PyInstaller extracts bundled files to a temp dir (`sys._MEIPASS`) that is deleted on exit.
- **Single-instance** enforcement uses a Windows named mutex (`Global\MusicAgentMutex`).
- **Notifications** use raw Win32 `Shell_NotifyIconW` via ctypes.
- **Rate limiting** wraps all Spotify API calls through `rate_limited_spotify_call()` at 1 call/sec.
- **redirect_uri** must be `http://127.0.0.1:8888/callback` (not localhost) to avoid Spotify "Insecure" errors. Must match the Spotify Developer Dashboard setting exactly.

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

```powershell
pyinstaller --noconsole --icon=poulet.ico --add-data ".\.env;." --name "Music Agent" main.py
```

The exe goes to `dist/Music Agent.exe`. See README.md for the full PowerShell one-liner that also copies to Startup folder and sets registry keys.

## First-Run Auth

The first launch (exe or script) opens a browser for Spotify OAuth. After that, the token auto-refreshes silently forever. The `.cache` file persists in AppData across restarts.

## Wake Device Limitation

The Spotify Web API can only transfer playback to devices that appear in the Spotify Connect device list (i.e. the Spotify client is running on them). Truly dormant devices (app closed, machine asleep) cannot be woken via the API.

## Common Pitfalls

- Never store the `.cache` token file relative to the `.env` path — it ends up in PyInstaller's temp dir and gets deleted.
- `sp.transfer_playback()` takes `device_id` (string), not `device_ids` (list).
- `.env` is encrypted by git-crypt — run `git-crypt unlock` after clone if you have the key.
- Environment variable keys are `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `SPOTIFY_DEVICE_ID`.
