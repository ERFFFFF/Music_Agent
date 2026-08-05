# Music Agent

Windows-only playback controller using global hotkeys. Runs as a background startup agent (PyInstaller exe).

**Two modes** (`config.py` `mode`, switched in Settings — both expose the same five actions, so `main.py` is mode-agnostic):

- **`cadence`** (default): drives a [Cadence](https://github.com/ERFFFFF/cadence) account. Cadence's audio *is* a browser tab, so the agent can't play anything itself — it POSTs an intent to `/api/remote/command` and the open Cadence tab drains it (~1/s) and performs it. No Spotify app, no `.env`, no secrets at build time → **the portable, single-exe mode** (`build_portable.ps1`), and it works where Spotify is blocked but HTTPS isn't.
- **`spotify`**: the original path — this machine's Spotify Connect device via the Web API, using a Spotify app's Client ID + Secret (asked for on first launch, stored in the same config file).

## First-run setup (both builds)

`main.is_configured()` is a config-dict check — a saved `cadence_session`, or a `spotify_client_id`.
When it's false, `main.run_setup()` shows `login_ui.choose_mode()` and then either the Cadence sign-in
or `login_ui.spotify_setup()` (which stores the keys via `config.save_spotify_credentials`).
The **installer no longer collects Spotify credentials** — that page forced a Spotify-only setup and
made Cadence unusable for an installed copy; the app asks instead, so both builds behave identically.
Settings reopens either window; a Settings save rebuilds the controller with `setup=False` so a hotkey
edit never triggers the chooser.

## Cadence mode specifics

- **`cadence.py`** is the whole mode: `CadenceClient` (HTTP + session persistence) and `CadenceController` (the five hotkey actions). Pure `requests`, no Windows API, so it can be exercised on any OS: `python cadence.py <url> <user> <pass>` runs a live self-check.
- **URL convention — the one that bites**: Cadence's public host is its Next UI, which rewrites `/api/:path*` to the backend root, stripping exactly ONE `/api`. So a backend route that is already `/api/...` needs it twice from outside (`/api/api/me`) and an unprefixed one needs it once (`/api/login`). `_request()` prepends `/api` to the backend's own path; don't "fix" it.
- **Login sends an `Origin` header.** Cadence's `/login` rejects a request carrying neither Origin nor Referer (login-CSRF guard). A native client has to state its origin; this is not a bypass.
- **One config file**: `cadence_config.txt` (JSON) beside the app — mode, Cadence URL + **session token**, Cloudflare token, Spotify keys, hotkeys. `config.app_dir()` = exe folder (frozen) or source folder (dev); AppData only when that is read-only. Replaced config.json + cadence_session.json + .env, which are migrated in once and then ignored. Written 0600; the session token in it IS a credential.
- **Session, not password**: the signed cookie round-trips through the config (`CadenceClient(session=…, on_session=…)`, wired by `cadence.client_from_config`). The password is never stored. **Starlette only re-issues the cookie when the session is MODIFIED** (verified in 1.3.1's source; an authenticated GET returns no Set-Cookie) — so Cadence's `_touch_session` marks it modified on `/api/remote/command|state`, which is what makes the agent's saved session slide instead of dying 7 days after login. Not done on `/api/remote/pending` (the browser's 1/s poll).
- **Cookie domain is load-bearing**: `_load_cookie` sets the restored cookie WITH the host domain. Without it the jar ends up with two entries named `session` (restored under domain "", server-issued under the host) and `cookies.get()` raises `CookieConflictError` — every hotkey dead on the second launch. `_cookie()` also reads defensively. `python cadence.py` runs the offline self-check that covers this.
- **`live` in the response is load-bearing**: it means a tab drained recently. False → the command will expire unperformed, and the agent says "No Cadence tab is open" instead of faking success.
- **Cloudflare Access**: when the Cadence host sits behind it, no native app can do the interactive login — a **service token** (`CF-Access-Client-Id/Secret`) is the headless path. It's entered via `login_ui.cloudflare_dialog()`, reached from the **⚙ in the sign-in window's top-right corner** and from the Settings account row (one dialog, two entry points, so the token is reachable before AND after signing in). Values are held in StringVars the dialog writes on Save; `CadenceClient` reads them at construction, so a change applies to the next client build. Access answers a token-less request with a **302 to `*.cloudflareaccess.com`**, which `requests` FOLLOWS into a 200 full of HTML; `_blocked_by_access()` checks the `www-authenticate: Cloudflare-Access` header *and* the redirect chain, because otherwise a hard block looks like an empty response.
- **Scale-to-zero**: Cadence stops after ~30 min idle and Sablier answers with a **200 `text/html`** wake page. A non-JSON body therefore raises "Cadence is waking up" instead of being read as an empty result. No retry: an asleep stack means no open tab, so nothing could perform the command anyway. `TIMEOUT = 25s` covers the wake.

## Portability

`config.data_dir()` keeps `cadence_config.txt` **next to the app** when that folder is writable, else AppData. One file holds everything, so a portable copy is exe + config — that pair is the whole install.

## Tech Stack

- **Python 3.10.5** on Windows 10+
- **spotipy** — Spotify Web API client with OAuth2
- **keyboard** — global hotkey listener (requires admin on some systems)
- **appdirs** — OS-specific persistent storage paths
- **ratelimit** — API call throttling (1 call/sec)
- **python-dotenv** — only `discover_device_id.py` (legacy `.env` reading); the app itself uses config.py
- **ctypes** — Windows shell notifications + single-instance mutex
- **PyInstaller** — builds `--onedir` exe (single process)
- **Inno Setup** — creates the Windows installer

## Project Structure

```
main.py                 # Mode selection, hotkeys, tray, Windows notifications
cadence.py              # Cadence mode: CadenceClient (HTTP/session) + CadenceController (actions)
login_ui.py             # Cadence sign-in window (shown on launch when there's no session)
spotify_backend.py      # Spotify mode: SpotifyController (device discovery + Web API actions)
config.py               # THE config: cadence_config.txt (paths, load/save, legacy migration)
build_portable.ps1      # One-file, no-installer build (dist\MusicAgent.exe)
discover_device_id.py   # Utility to list Spotify Connect devices
cadence_config.txt      # (generated, gitignored) mode + Cadence URL/session + Cloudflare + Spotify + hotkeys
.env / .env.template    # legacy Spotify credentials — read once and migrated, no longer written
.gitattributes          # git-crypt encryption rules
requirements.txt        # Python dependencies
poulet.ico              # App icon for exe and installer
Music Agent.spec        # PyInstaller build spec (gitignored)
installer/              # Installer files (Inno Setup script, build script, info text)
```

## Key Architecture Decisions

- **Config is one file**, `cadence_config.txt`, next to the app (see above). The old `.env` / `config.json` / `cadence_session.json` are migrated in once and then left alone; nothing writes them any more.
- **The installer no longer creates a `.env`** — it installs files only, and the app collects whatever the chosen mode needs on first launch.
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
