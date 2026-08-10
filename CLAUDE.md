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
- **Credentials are encrypted at rest with Windows DPAPI** (`config.SECRET_FIELDS`, `enc:<base64>` values): URL, session, Cloudflare and Spotify keys. `mode` and `hotkeys` stay plaintext so a copied config still looks sane and keeps its shortcuts. `CryptProtectData` keys the blob to the **Windows account**, so the trade-off is deliberate: no launch password and no key inside the exe, at the cost of secrets not surviving a move to another PC/user — `_decrypt` returns `""` there, `is_configured()` reads that as "not signed in", and the user signs in once. `load_config` re-encrypts a plaintext file on sight, so an upgraded install doesn't leave its old token lying around. `python config.py` covers all of it.
- **Session, not password**: the signed cookie round-trips through the config (`CadenceClient(session=…, on_session=…)`, wired by `cadence.client_from_config`). The password is never stored. **Starlette only re-issues the cookie when the session is MODIFIED** (verified in 1.3.1's source; an authenticated GET returns no Set-Cookie) — so Cadence's `_touch_session` marks it modified on `/api/remote/command|state`, which is what makes the agent's saved session slide instead of dying 7 days after login. Not done on `/api/remote/pending` (the browser's 1/s poll).
- **Cookie domain is load-bearing**: `_load_cookie` sets the restored cookie WITH the host domain. Without it the jar ends up with two entries named `session` (restored under domain "", server-issued under the host) and `cookies.get()` raises `CookieConflictError` — every hotkey dead on the second launch. `_cookie()` also reads defensively. `python cadence.py` runs the offline self-check that covers this.
- **`live` in the response is load-bearing**: it means a tab drained recently. False → the command will expire unperformed, and the agent says "No Cadence tab is open" instead of faking success.
- **Cloudflare Access**: when the Cadence host sits behind it, no native app can do the interactive login — a **service token** (`CF-Access-Client-Id/Secret`) is the headless path. It's entered via `login_ui.cloudflare_dialog()`, reached from the **⚙ in the sign-in window's top-right corner** and from the Settings account row (one dialog, two entry points, so the token is reachable before AND after signing in). Values are held in StringVars the dialog writes on Save; `CadenceClient` reads them at construction, so a change applies to the next client build. Access answers a token-less request with a **302 to `*.cloudflareaccess.com`**, which `requests` FOLLOWS into a 200 full of HTML; `_blocked_by_access()` checks the `www-authenticate: Cloudflare-Access` header *and* the redirect chain, because otherwise a hard block looks like an empty response.
- **Scale-to-zero**: Cadence stops after ~30 min idle and Sablier answers with a **200 `text/html`** wake page. A non-JSON body therefore raises "Cadence is waking up" instead of being read as an empty result. No retry: an asleep stack means no open tab, so nothing could perform the command anyway. `TIMEOUT = 25s` covers the wake.

## Portability

`config.data_dir()` keeps `cadence_config.txt` **next to the app** when that folder is writable, else AppData. One file holds everything, so a portable copy is exe + config — that pair is the whole install.

## Tech Stack

- **Python >=3.10,<3.16** on Windows 10+
- **requests** — the only network library; both backends are plain HTTP
- **keyboard** — global hotkey listener (requires admin on some systems)
- **customtkinter / pystray / Pillow** — the tray app only (`requirements-gui.txt`). `cli.py` imports none of them.
- **ctypes** — Windows shell notifications + single-instance mutex
- **PyInstaller** — pinned in `build_portable.ps1`, the only thing that uses it
- **Inno Setup** — creates the Windows installer

**Deliberately not dependencies.** Four were removed rather than replaced like-for-like; don't reintroduce them:

| Was | Now | Why |
|-----|-----|-----|
| `spotipy` | `requests` calls in `spotify_backend.py` | Pulled in **redis** as a hard dependency, bundled into the exe, for ten REST calls and an OAuth exchange |
| `appdirs` | `config.appdata_dir()` | Unmaintained since 2020, for one `os.path.join`. Emits the identical path, so existing installs still migrate |
| `ratelimit` | nothing | Throttled to 1 call/sec — a ceiling a hotkey press can't reach, while forcing a real `sleep` inside `play_pause` |
| `python-dotenv` | `config._read_env()` | Only `discover_device_id.py` used it, which duplicated `cli.py devices` and read a config format the app had abandoned |

## Project Structure

```
main.py                 # Tray app: mode selection, hotkeys, tray, Windows notifications
cli.py                  # Same app without a GUI: setup/status/hotkeys/one-shot actions/headless run
cadence.py              # Cadence mode: CadenceClient (HTTP/session) + CadenceController (actions)
login_ui.py             # Cadence sign-in window (shown on launch when there's no session)
settings_ui.py          # Settings window (mode, account, hotkey capture)
spotify_backend.py      # Spotify mode: SpotifyController (OAuth + Web API actions), requests only
config.py               # THE config: cadence_config.txt (DPAPI at rest, load/save, migration, ACTIONS)
build_portable.ps1      # dist\MusicAgent_portable.exe; -Cli builds the GUI-free MusicAgent_cli.exe
cadence_config.txt      # (generated, gitignored) mode + Cadence URL/session + Cloudflare + Spotify + hotkeys
.env.example            # legacy Spotify credential format, for migrating an old install only
                        # (a real .env is gitignored — never commit one)
requirements.txt        # Core deps — enough for cli.py
requirements-gui.txt    # Adds the tray app's GUI deps on top
poulet.ico              # App icon for exe and installer
Music Agent.spec        # PyInstaller build spec (gitignored)
installer/              # Installer files (Inno Setup script, build script, info text)
```

**The GUI/CLI split is load-bearing.** `cli.py`'s import graph must never reach `customtkinter`,
`pystray` or `PIL` — that is what makes `build_portable.ps1 -Cli` a two-package build. Anything both
front ends need (`client_from_config`, `normalize_url`, `find_icon`, `is_configured`, `ACTIONS`) belongs
in `config.py` or `cadence.py`, never in a `*_ui.py`. To check after a change:

```bash
python cli.py selftest    # also asserts every subcommand resolves to a handler
```

## Key Architecture Decisions

- **Config is one file**, `cadence_config.txt`, next to the app (see above). The old `.env` / `config.json` / `cadence_session.json` are migrated in once and then left alone; nothing writes them any more.
- **The installer no longer creates a `.env`** — it installs files only, and the app collects whatever the chosen mode needs on first launch.
- **The Spotify refresh token lives in `cadence_config.txt`** with everything else — no separate AppData cache file. `_Auth.on_token` persists it, and Spotify may hand back a *new* refresh token on any refresh, so it is re-saved whenever the response carries one.
- **Changing the Spotify Client ID clears the refresh token** (`config.save_spotify_credentials`) — a token issued by the old app can only fail.
- **Device discovery is on demand**, in `SpotifyController._device()`: prefers the active device, else the first. No background rescan thread — the next hotkey press re-runs it anyway, so the timer only ever reached the same answer sooner. A 404 clears the cached id.
- **Single-instance** enforcement uses a Windows named mutex (`Global\MusicAgentMutex`).
- **Notifications** use raw Win32 `Shell_NotifyIconW` via ctypes.
- **No API throttle.** A hotkey press cannot approach Spotify's rate limit; the old 1-call/sec gate only added a real `sleep` between the two calls `play_pause` makes. Spotify's own 429 is handled in `_api` and reported with its `Retry-After`.
- **redirect_uri** must be a loopback IP literal with a port — `http://127.0.0.1:8888/callback`, not `localhost`. `_Auth._authorize` refuses anything else before opening a browser. Must match the Spotify Developer Dashboard setting exactly.
- **Both backends return messages, never exceptions**, to the front ends: `CadenceController._send` and `SpotifyController._act` both catch and return the sentence to display. Hotkeys run on the keyboard thread, where an uncaught exception is a permanently dead key.

### Spotify Web API traps (verified against developer.spotify.com, 2026-08-10)

These are the ones that fail *silently* or look like something else. Don't undo them.

- **Never test the status code to decide whether to call `.json()` — test the body.** `play`/`pause`/`next`/`previous` answer **204 empty**; `GET /me/player` answers **204 empty** when nothing is playing; and `PUT`/`DELETE /me/library` answer **200 with an empty body**. A `if status == 204` guard alone crashes on the third case. `_api` tests `not r.content`.
- **`/me/tracks` is deprecated — use `/me/library`.** It takes full `spotify:track:{id}` **URIs** in a `uris` query param, not bare `ids`. `requests` percent-encodes the colons, which is the form the docs show. `GET /me/library/contains` still returns a JSON array of booleans.
- **`next`/`previous` are `POST`**, not `PUT`. `device_id` is a **query** param on all four playback writes.
- **Refresh tokens expire after 6 months** — plus password changes and app removal. `_Auth.token()` treats `invalid_grant` as scheduled: it clears the token here *and* in the config, then falls through to consent. Turning that back into a hard error strands the app with no route back except hand-editing `cadence_config.txt`.
- **Premium is required for all five playback writes**, not just `play` — a free account 403s on everything.
- **Ordering between Player calls is not guaranteed.** Never add a confirming read after a write; `play_pause` reads first only because a toggle cannot do otherwise.
- `localhost` is **not** a valid redirect URI (since Nov 2025) — a loopback IP literal with a port is. `_Auth._authorize` refuses anything else before opening a browser.

### The consent server (measured failures — don't undo these)

Browser consent runs on the **calling thread, which is the hotkey thread**. Everything here exists because of that.

- `Handler.timeout = 10` bounds *reading* a request. `HTTPServer.timeout` bounds only *waiting for a connection* — with it alone, any socket that connects and sends nothing (browser pre-connect, security-product probe, port scan) parks `handle_one_request()` in `rfile.readline()` forever and **every hotkey stays dead until the app is killed**. Reproduced before the fix; after it, an idle socket costs 10s and the real callback still lands.
- The server binds `parsed.hostname`, not a hardcoded `127.0.0.1`, with `address_family` to match. An app registered on `http://[::1]:PORT` passed validation and could then never receive its redirect.
- `_consent_lock` + `_consent_blocked_until`: one consent at a time, and a cooldown after a failure. Without it, every hotkey press during or after an abandoned consent queued another browser tab plus another full `CONSENT_TIMEOUT` block.
- `cli.py setup` authorises immediately after saving credentials, so the hotkey path normally never reaches consent at all.

**Known, not fixed:** the GUI's Settings → *Credentials…* does not authorise up-front, so on that path the *first* hotkey press still blocks for up to `CONSENT_TIMEOUT` while the browser is open. Doing it inline there would freeze the Settings window's mainloop for the same duration, which is worse; the real fix is a non-blocking consent (thread + a "waiting for Spotify" state), and it needs testing on Windows.

### Config writes

**Any new credential field must be added to `SECRET_FIELDS`**, or it sits in plaintext next to six encrypted siblings. `spotify_refresh_token` is in it for exactly that reason. `selftest.py` asserts the coverage against a named list, so adding a credential to `DEFAULTS` and forgetting to seal it fails the check rather than shipping.

`update_config` composes with DPAPI for free: `load_config` decrypts, `save_config` encrypts, so the dict callers hold is always plaintext and the file is always sealed.

**Long-lived objects must persist through `config.update_config(key, value, cfg)`, never `save_config(cfg)`.** A `CadenceClient` re-saves its sliding cookie and a Spotify `_Auth` rotates its refresh token minutes or hours after being constructed; writing their captured dict reverted whatever Settings saved in between. Measured: one cookie rotation put an old hotkey back on disk. `update_config` re-reads, sets one field, writes. It deliberately does **not** resurrect unsaved in-memory edits — every caller saves before signing in.

`selftest.py` pins this (`a credential write never reverts a settings save`).

### tkinter variables

Every `ctk.StringVar` passes an explicit `master=`. A masterless one attaches to `_default_root` — the first root created, released only when *destroyed*, not when withdrawn. Opened from the tray, Settings withdraws its root, so the variable ended up in a different Tcl interpreter than its entry widget: **fields rendered blank and nothing typed was ever read back**, making Settings → Sign in impossible to complete. It only ever worked at launch because those windows destroy each root before the next opens.

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
- **There is no git-crypt here any more.** `.gitattributes` declared a git-crypt filter for `.env`, but
  the committed blobs were plaintext the whole time — the Spotify Client ID/Secret sat in the PUBLIC
  history until 2026-08-05, when `.env` was purged from every commit. Anything that was in it must be
  treated as compromised and rotated; purging history does not un-publish it.
  `.env` is gitignored now; credentials live in `cadence_config.txt` (also gitignored), never in git.
- Environment variable keys are `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`.
- The installer writes `.env` next to the exe. `find_env()` checks there first before `_MEIPASS`.
