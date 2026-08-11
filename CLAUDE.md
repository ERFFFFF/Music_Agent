# Music Agent

Windows-only playback controller using global hotkeys. Runs as a background startup agent (PyInstaller exe).

**Two modes** (`config.py` `mode`, switched in Settings — both expose the same five actions, so `ui/tray.py` is mode-agnostic):

- **`cadence`** (default): drives a [Cadence](https://github.com/ERFFFFF/cadence) account. Cadence's audio *is* a browser tab, so the agent can't play anything itself — it POSTs an intent to `/api/remote/command` and the open Cadence tab drains it (~1/s) and performs it. No Spotify app and no secrets at build time → **the portable, single-exe mode** (`build_portable.ps1`), and it works where Spotify is blocked but HTTPS isn't.
- **`spotify`**: the original path — this machine's Spotify Connect device via the Web API, using a Spotify app's Client ID + Secret (asked for on first launch, stored in the same config file).

## First-run setup (both builds)

`config.is_configured()` is a config-dict check — a saved `cadence_session`, or a `spotify_client_id`.
When it's false, `ui.tray.run_setup()` shows `ui.login.choose_mode()` and then either the Cadence sign-in
or `ui.login.spotify_setup()` (which stores the keys via `config.save_spotify_credentials`).
The **installer no longer collects Spotify credentials** — that page forced a Spotify-only setup and
made Cadence unusable for an installed copy; the app asks instead, so both builds behave identically.
Settings reopens either window; a Settings save rebuilds the controller with `setup=False` so a hotkey
edit never triggers the chooser.

## Cadence mode specifics

- **`backends/cadence.py`** is the whole mode: `CadenceClient` (HTTP + session persistence) and `CadenceController` (the five hotkey actions). Pure `httpmin`, no Windows API, so it can be exercised on any OS: `python -m music_agent now` runs a live self-check.
- **URL convention — the one that bites**: Cadence's public host is its Next UI, which rewrites `/api/:path*` to the backend root, stripping exactly ONE `/api`. So a backend route that is already `/api/...` needs it twice from outside (`/api/api/me`) and an unprefixed one needs it once (`/api/login`). `_request()` prepends `/api` to the backend's own path; don't "fix" it.
- **Login sends an `Origin` header.** Cadence's `/login` rejects a request carrying neither Origin nor Referer (login-CSRF guard). A native client has to state its origin; this is not a bypass.
- **The `.env` is the CLI's, and the CLI's alone.** `config._env_enabled` is OFF by default and only `cli.main()` calls `config.use_env(True)`, so `env_path()` returns `""` in the tray app and every `.env` path below is dead there: the GUI is configured from `cadence_config.txt` through its own windows. Fail closed — a new front end reads the config file until someone deliberately opts it in. `tests/test_tray.py` checks it in a subprocess, because the flag is process-global and another test turning it on would make an in-process check pass for the wrong reason.
- **Two files, and only one holds secrets** (in the CLI): `.env` beside the app is every credential (server, account, Cloudflare token, Spotify keys) plus the `HOTKEY_*` shortcuts — see `config.ENV_FIELDS` / `ENV_HOTKEYS` — read on every load and **never written to**. `cadence_config.txt` (JSON, same folder) holds what the app EARNS or the user picks: **session token**, refresh token, mode, and the hotkeys the `.env` doesn't name. See *What survives what* below before touching either. `config.app_dir()` = exe folder (frozen) or source folder (dev); AppData only when that is read-only. Written 0600; the session token in it IS a credential.
- **Credentials are encrypted at rest with Windows DPAPI** (`config.SECRET_FIELDS`, `enc:<base64>` values): URL, session, Cloudflare and Spotify keys. `mode` and `hotkeys` stay plaintext so a copied config still looks sane and keeps its shortcuts. `CryptProtectData` keys the blob to the **Windows account**, so the trade-off is deliberate: no launch password and no key inside the exe, at the cost of secrets not surviving a move to another PC/user — `_decrypt` returns `""` there, `is_configured()` reads that as "not signed in", and the user signs in once. `load_config` re-encrypts a plaintext file on sight, so an upgraded install doesn't leave its old token lying around. `python -m pytest tests/test_config.py` covers all of it.
- **Session, not password**: the signed cookie round-trips through the config (`CadenceClient(session=…, on_session=…)`, wired by `backends.cadence.client_from_config`). The password is never stored. **Starlette only re-issues the cookie when the session is MODIFIED** (verified in 1.3.1's source; an authenticated GET returns no Set-Cookie) — so Cadence's `_touch_session` marks it modified on `/api/remote/command|state`, which is what makes the agent's saved session slide instead of dying 7 days after login. Not done on `/api/remote/pending` (the browser's 1/s poll).
- **Cookie domain is load-bearing**: `_load_cookie` sets the restored cookie WITH the host domain. Without it the jar ends up with two entries named `session` (restored under domain "", server-issued under the host) and `cookies.get()` raises `CookieConflictError` — every hotkey dead on the second launch. `_cookie()` also reads defensively. `python -m pytest tests/test_cadence.py` runs the offline self-check that covers this.
- **`live` in the response is load-bearing**: it means a tab drained recently. False → the command will expire unperformed, and the agent says "No Cadence tab is open" instead of faking success.
- **The server's pending queue drops the occasional command under burst — not ours to fix.** Measured 2026-08-11 against a stand-in tab polling `/api/remote/pending` at 0.8s: commands submitted **faster than the tab polls** are occasionally lost server-side (2 lost out of ~20 burst commands across two runs; one run sent 6 in 1.2s and 5 arrived). Commands spaced ≥1.2s apart arrived 100% of the time, over dozens of tries. `command()` returns `live=True` for the lost ones — the server accepted them, so nothing here can detect it. This is Cadence's queue, in the other repo, and it is invisible to real hotkey use (nobody presses 6 keys in 1.2s meaningfully). **Do not add a retry or an ack to compensate**: a retried playback intent is a *double* skip when the first one did land, which is worse than the drop.
- **Cloudflare Access**: when the Cadence host sits behind it, no native app can do the interactive login — a **service token** (`CF-Access-Client-Id/Secret`) is the headless path. It's entered via `ui.login.cloudflare_dialog()`, reached from the **⚙ in the sign-in window's top-right corner** and from the Settings account row (one dialog, two entry points, so the token is reachable before AND after signing in). Values are held in StringVars the dialog writes on Save; `CadenceClient` reads them at construction, so a change applies to the next client build. Access answers a token-less request with a **302 to `*.cloudflareaccess.com`**, which the HTTP client FOLLOWS into a 200 full of HTML; `_blocked_by_access()` checks the `www-authenticate: Cloudflare-Access` header *and* the redirect chain, because otherwise a hard block looks like an empty response.
- **Corporate proxy: `PROXY` in the .env, exported to the ENVIRONMENT, not plumbed through the code.** `config.apply_proxy()` (called from `load_config`, so both front ends get it — the GUI from its own Settings fields, the CLI from those or the `.env`) copies `PROXY` / `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` into `os.environ`, where the `ProxyHandler` already inside every opener `httpmin` builds picks them up — CONNECT tunnelling and `NO_PROXY` bypass included. Passing a proxy argument down through both backends would have been the same behaviour and three more parameters. The `.env` beats an existing shell variable, so "what the .env supplies, the .env owns" has no exception.
  **The symptom of a missing proxy is a DNS error, not a timeout**: on a network that mandates one, the client is not supposed to resolve external names — the proxy does — so you get `getaddrinfo failed` (WSA 11001) and it reads as broken DNS. `status` therefore prints the proxy line even when unset. Verified 2026-08-11 against a real loopback CONNECT proxy: `PROXY=` sets both schemes and a live Cadence login went through it (3 CONNECTs observed), and a dead proxy FAILS rather than silently going direct. For NTLM/Kerberos proxies and PAC files, see `PROXY_AUTH` below.
- **`PROXY_AUTH=current-user` swaps the whole TRANSPORT to `net/winhttp.py`** (ctypes over Windows' own HTTP stack). urllib speaks Basic proxy auth only — measured against proxies demanding NTLM and Negotiate, it never attempts either and just surfaces the 407 — and there is no way to add SSPI in pure Python. WinHTTP does it with one option flag, and reads PAC files too. `httpmin.use_windows_transport()` is the single branch; both backends and both front ends ride on it. OFF unless the .env asks, so every network that already works keeps the tested path.
- **WINDOWS 10 CANNOT DO TLS 1.3, which makes that transport unusable against a TLS-1.3-only host.** Measured 2026-08-11: this project's own Cadence server rejects a TLS 1.2 handshake (`TLSV1_ALERT_PROTOCOL_VERSION`), Windows 10 build 19045's Schannel stops at 1.2, and so **every** Windows-stack client fails — WinHTTP, and `curl.exe` (Schannel) identically — while Python succeeds because it ships OpenSSL. It is not a certificate problem: relaxing every cert check changes nothing. `net.winhttp._ERRORS[12175]` says all of this in the error rather than printing a code. The fix is at the server (Cloudflare -> SSL/TLS -> Edge Certificates -> Minimum TLS Version -> 1.2) or to leave `PROXY_AUTH` unset. Windows 11 is unaffected. **Do not "fix" this in the client — there is nothing to fix there.**
- **Scale-to-zero**: Cadence stops after ~30 min idle and Sablier answers with a **200 `text/html`** wake page. A non-JSON body therefore raises "Cadence is waking up" instead of being read as an empty result. No retry: an asleep stack means no open tab, so nothing could perform the command anyway. `TIMEOUT = 25s` covers the wake.

## Portability

`config.data_dir()` keeps `cadence_config.txt` **next to the app** when that folder is writable, else AppData, and `config.env_path()` looks for `.env` in the same two places — when this process is allowed one at all. A portable copy is app + `.env` + config — and only the first two matter, because the third rebuilds itself.

## What survives what (measured 2026-08-11, not reasoned about)

`cadence_config.txt` is a **cache of things the app EARNED**, not a second copy of the configuration.
Nothing else in this file is worth understanding before this table:

| Event | Result |
|-------|--------|
| Restart `run`, or any one-shot command | Works. Every CLI invocation is already a cold process; the session cookie and refresh token come off disk |
| Delete `cadence_config.txt` | **Still works.** Cadence re-logs in from `USERNAME`/`PASSWORD`; Spotify re-adopts its token. Lost: `mode`, and any hotkey the `.env` does not name |
| Delete `.env` | **Cadence mode breaks** (`cadence_url` lived only there — the session survives with nothing to point it at). Spotify keeps working until the refresh token dies. This asymmetry is the price of "one place to rotate a key" and is intended |
| Reboot / log out | No effect — DPAPI decrypts for the same Windows account |
| Copy to another PC or Windows user | Every `SECRET_FIELDS` value reads as `""`; `is_configured()` sees "not signed in". With the `.env` alongside, the re-sign-in is automatic and invisible |

The one genuinely unrecoverable thing is `spotify_refresh_token`: it is *earned* at browser consent and cannot be re-derived from the `.env`, so deleting the config file in Spotify mode costs one consent click. `backends.spotify._legacy_refresh_token()` softens that by adopting the old spotipy `%LOCALAPPDATA%\...\.cache` if one is still there.

**The filename is historical.** It predates Spotify mode sharing the file and now describes a third of what it holds. Renaming it needs a migration read (like `legacy_paths()` does) or every existing install silently signs itself out — don't rename it casually.

## Tech Stack

- **Python >=3.10,<3.16** on Windows 10+
- **The CLI has NO third-party dependencies at all.** `requirements-cli.txt` is empty on purpose; `music_agent/cli.py` runs on the standard library. Verify with the site-packages sweep, not by eye:
  `python -c "import music_agent.cli, music_agent.win32.hotkeys, sys; print([m.__name__ for m in sys.modules.values() if 'site-packages' in (getattr(m,'__file__','') or '')])"` — only `_distutils_hack` / `pywin32_bootstrap` (injected by `.pth` files at interpreter startup) may appear.
- **httpmin.py** — the HTTP layer, `urllib.request` in a `requests` shape. Both backends import it, so the GUI is dependency-free here too.
- **win32/hotkeys.py** — Win32 `RegisterHotKey` + a message loop. The CLI's hotkeys.
- **keyboard** — the TRAY app only. It survives because `ui/settings.py` **records** a combination as you press it, which needs a keyboard hook; `RegisterHotKey` cannot do that.
- **customtkinter / pystray / Pillow** — the tray app only (`requirements-gui.txt`). `music_agent/cli.py` imports none of them.
- **ctypes** — Windows shell notifications, single-instance mutex, DPAPI, hotkeys
- **PyInstaller** — pinned in `build_portable.ps1`, the only thing that uses it
- **Inno Setup** — creates the Windows installer

**Deliberately not dependencies.** Six were removed rather than replaced like-for-like; don't reintroduce them:

| Was | Now | Why |
|-----|-----|-----|
| `requests` | `net/httpmin.py` | ~140 lines of `urllib.request`. Both backends make small JSON calls and nothing else, so the package bought bytes, not behaviour. What it DID buy is spelled out in httpmin's docstring — non-2xx as data, empty bodies, the redirect chain, cookie domain collapsing — and each one has a self-check |
| `keyboard` (CLI) | `win32/hotkeys.py` | `RegisterHotKey` is the OS's own global-hotkey API: no `WH_KEYBOARD_LL` hook over every keystroke, and no admin rights. Trade: a combination another app owns is REFUSED (error 1409) instead of stolen |
| `spotipy` | hand-written calls in `backends/spotify.py` | Pulled in **redis** as a hard dependency, bundled into the exe, for a dozen REST calls and an OAuth exchange |
| `appdirs` | `config.appdata_dir()` | Unmaintained since 2020, for one `os.path.join`. Emits the identical path, so existing installs still migrate |
| `ratelimit` | nothing | Throttled to 1 call/sec — a ceiling a hotkey press can't reach, while forcing a real `sleep` inside `play_pause` |
| `python-dotenv` | `config._read_env()` | 20 lines, and it has to NOT strip inline `#` (passwords contain them) — which dotenv does |

## Project Structure

```
pyproject.toml          # metadata, the (empty) dependency list, entry points, pytest + ruff config
tray_entry.py           # PyInstaller's entry script -- it needs a file, not a `-m` module path
.env                    # (gitignored) EVERY credential + MODE + HOTKEY_* + proxy. Never written to.
.env.example            # ...documented, key by key
cadence_config.txt      # (generated, gitignored) session cookie + refresh token + what the .env omits
src/music_agent/        # THE PACKAGE (src layout: only an installed copy is importable, so a test
  __main__.py           #   run cannot silently pick up the source tree instead of the built thing)
  cli.py                #   `python -m music_agent` -- the CLI, and with no args the hotkey agent
  config.py             #   the .env, cadence_config.txt, DPAPI, the proxy. Depends on net/ only.
  log.py                #   the in-memory ring buffer every front end displays. No file, ever.
  net/                  #   httpmin.py (urllib, default) + winhttp.py (Windows stack, proxy SSPI)
  backends/             #   cadence.py + spotify.py -- one controller each, identical contract
  win32/                #   hotkeys.py -- RegisterHotKey. Windows-only, imported lazily.
  ui/                   #   THE ONLY PART WITH DEPENDENCIES. `python -m music_agent.ui`
tests/                  # pytest. One test_<module>.py per module + test_integration.py for the rest.
requirements-cli.txt    # empty, and that is the point
requirements-gui.txt    # customtkinter + pystray + Pillow + keyboard, for ui/ alone
build_portable.ps1      # dist\MusicAgent_portable.exe -- the TRAY app only
poulet.ico              # App icon for exe and installer
installer/              # Installer files (Inno Setup script, build script, info text)
```

Imports only ever point downwards through those layers, and nothing in the package imports `tests/`.
**Run the checks with `python -m pytest`** — the per-module `selftest()` functions moved into `tests/`
when the package moved under `src/`, so the app no longer ships its own test code.

**The GUI/CLI split is load-bearing.** `music_agent/cli.py`'s import graph must never reach
`customtkinter`, `pystray`, `PIL` or `keyboard` — that is what makes `requirements-cli.txt` empty.
**The CLI is deliberately NOT frozen into an exe**: it has nothing to bundle, so an exe would only add
a 10 MB artifact and a rebuild between every edit, to ship an interpreter the machine already has. It
runs live from source; only the tray app earns a build, because it starts from a shortcut with no
console. Anything both front ends need (`client_from_config`, `normalize_url`, `find_icon`,
`is_configured`, `ACTIONS`) belongs in `config.py` or `backends/cadence.py`, never in `ui/`. `win32.hotkeys`
is imported INSIDE `cmd_run`, not at module top, because it is the only Windows-only module in that
graph and every other command runs anywhere. To check after a change:

```bash
python -m pytest    # every check: per-module and cross-module
```

## Key Architecture Decisions

- **The `.env` is an INPUT, not a legacy format to absorb** — and an input only the CLI has. `config.env_config()` overlays it onto the loaded config LAST, so an edit there beats whatever an earlier run left in `cadence_config.txt`. `save_config` then blanks every `SECRET_FIELDS` entry the `.env` supplies — without that, each save would copy the secret into the config file too, and rotating a leaked key would mean editing two files with the forgotten copy still working. `config.demo()` pins both halves.
- **`cadence_username` / `cadence_password` are deliberately absent from `DEFAULTS`.** They exist only in `ENV_FIELDS` and in memory, so `save_config` — which is built from `DEFAULTS` — *cannot* write an account password to disk even by accident. The app stores the session it exchanges them for.
- **Shortcuts come from the `.env` too**, one key per action: `ENV_HOTKEYS` is derived from `ACTIONS` (`HOTKEY_` + the action id upper-cased), so adding an action cannot leave it unconfigurable. They are NOT in `ENV_FIELDS` because `hotkeys` is a nested dict: `env_config()` returns a PARTIAL one and both `load_config` and `save_config` merge action-by-action. Returning a complete dict there would silently reset the four the `.env` didn't name.
- **A `.env`-owned shortcut is not persisted either** — `save_config` writes the DEFAULT for it. Storing the `.env`'s value would strand it: delete the `HOTKEY_` line later and the shortcut it set would live on out of the config file. `cmd_hotkeys` refuses to rebind one, for the same reason `cmd_set` refuses a field.
- **`_read_env` must not strip an inline `#`.** A password is far likelier to contain one than a line is to carry a trailing note, and truncating a password produces a login failure nobody can explain. This is also why `python-dotenv` is not the answer here.
- **The installer no longer creates a `.env`** — it installs files only, and the app collects whatever the chosen mode needs on first launch.
- **Both controllers expose `last_error`.** Every action returns TEXT rather than raising (a hotkey must not die on an error), which leaves a *script* unable to tell `"Liked"` from `"session expired"`. `cmd_control` reads `last_error` to pick its exit code; without it the CLI exits 0 on commands that plainly did not happen.
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
- **A SUCCESS IS NOT RELIABLY JSON, and the status does not tell you.** Measured live 2026-08-11: `PUT /me/player/play` and `/pause` answer **200 with an opaque 27-character token and no `Content-Type`** (`A3_PtwniwTUebcLjlJgM73TGjkU`). Undocumented. Testing the status alone crashes on the empty-200 library calls; testing the body alone crashes on this one — the pause HAD worked and the app said "Spotify sent something unexpected". `_api` now treats an unparseable body as `{}`: every caller either ignores a write's result or reads a documented shape, and a real failure was a status code above.
- **403 is not always Premium.** `Player command failed: Restriction violated` is what a skip gets when the current context has nowhere to skip to. Telling a Premium subscriber to buy Premium is unactionable, so `_api` relays Spotify's own `error.message` (via `_error_message`) and keeps the Premium sentence for when the message actually says so.
- **Some devices 403 a command they then perform.** Measured against a librespot Connect device (`music_streamer`): `POST /me/player/next` answers 403 `Restriction violated` *and the track still changes*, with or without `device_id`. The same call against the Spotify desktop client answers 204 and works. This is device-side, not ours — do NOT start treating that 403 as success, or a genuine "can't skip" becomes silent.

### The consent server (measured failures — don't undo these)

Browser consent runs on the **calling thread, which is the hotkey thread**. Everything here exists because of that.

- `Handler.timeout = 10` bounds *reading* a request. `HTTPServer.timeout` bounds only *waiting for a connection* — with it alone, any socket that connects and sends nothing (browser pre-connect, security-product probe, port scan) parks `handle_one_request()` in `rfile.readline()` forever and **every hotkey stays dead until the app is killed**. Reproduced before the fix; after it, an idle socket costs 10s and the real callback still lands.
- The server binds `parsed.hostname`, not a hardcoded `127.0.0.1`, with `address_family` to match. An app registered on `http://[::1]:PORT` passed validation and could then never receive its redirect.
- `_consent_lock` + `_consent_blocked_until`: one consent at a time, and a cooldown after a failure. Without it, every hotkey press during or after an abandoned consent queued another browser tab plus another full `CONSENT_TIMEOUT` block.
- `music_agent/cli.py setup` authorises immediately after saving credentials, and `login` does it on demand, so the hotkey path normally never reaches consent at all.
- **Every hotkey press prints a line** (`cli.hotkey_action`), including the three actions whose controller returns `None` — those print `ok`. Only `show_current` and `toggle_like` return text, so before this a working `next_track` was indistinguishable from a key that never fired, and the only way to check was to go and listen to the music. Failures print `FAILED <reason>` to stderr, decided by `controller.last_error` rather than by the text (the controllers return a string either way). `flush=True` because stdout is block-buffered when redirected, and someone tailing a log to check their keys work must not wait for 8 KB. It is module level, not a closure in `cmd_run`, so the self-check can exercise it without a keyboard.
- **A bare `python -m music_agent` runs the hotkey agent.** The subparsers are `required=False` and the DEFAULT lives on the subparsers ACTION (`sub.default = "run"`), not on `parser.set_defaults` — argparse applies action defaults first, so a `set_defaults(command=...)` is overwritten by the subparser's own `None`. This exists because `required=True` made the obvious command print a usage error at someone whose intent was the app's whole purpose: start it, leave it up, press keys. The one-shot verbs are the extra.

**Known, not fixed:** the GUI's Settings → *Credentials…* does not authorise up-front, so on that path the *first* hotkey press still blocks for up to `CONSENT_TIMEOUT` while the browser is open. Doing it inline there would freeze the Settings window's mainloop for the same duration, which is worse; the real fix is a non-blocking consent (thread + a "waiting for Spotify" state), and it needs testing on Windows.

### Config writes

**Any new credential field must be added to `SECRET_FIELDS`**, or it sits in plaintext next to six encrypted siblings. `spotify_refresh_token` is in it for exactly that reason. `tests/test_integration.py` asserts the coverage against a named list, so adding a credential to `DEFAULTS` and forgetting to seal it fails the check rather than shipping.

`update_config` composes with DPAPI for free: `load_config` decrypts, `save_config` encrypts, so the dict callers hold is always plaintext and the file is always sealed.

**Long-lived objects must persist through `config.update_config(key, value, cfg)`, never `save_config(cfg)`.** A `CadenceClient` re-saves its sliding cookie and a Spotify `_Auth` rotates its refresh token minutes or hours after being constructed; writing their captured dict reverted whatever Settings saved in between. Measured: one cookie rotation put an old hotkey back on disk. `update_config` re-reads, sets one field, writes. It deliberately does **not** resurrect unsaved in-memory edits — every caller saves before signing in.

`tests/test_integration.py::test_credential_write_does_not_clobber` pins this.

### tkinter variables

Every `ctk.StringVar` passes an explicit `master=`. A masterless one attaches to `_default_root` — the first root created, released only when *destroyed*, not when withdrawn. Opened from the tray, Settings withdraws its root, so the variable ended up in a different Tcl interpreter than its entry widget: **fields rendered blank and nothing typed was ever read back**, making Settings → Sign in impossible to complete. It only ever worked at launch because those windows destroy each root before the next opens.

### Logging

- **One buffer, three front ends.** `music_agent.log` is a `logging.Handler` over a bounded deque; `python -m music_agent -d` prints it to the console and the tray app's **Settings -> Logs** tab shows it live. What you see in the window is what the CLI would have printed, which is the point — a problem reported from one front end is reproducible in the other.
- **Deliberately no file.** A log on disk is a support burden (where, how big, who deletes it) and a privacy question: the lines name the server and the account. It dies with the process. Do not "improve" this by adding a FileHandler without asking.
- **Readers POLL, they are not called back.** Lines arrive from the keyboard thread and from whatever thread an HTTP call runs on; Tk may only be touched from the thread owning the widget. `log.since(cursor)` lets the window ask on its own `after()` timer — that is the entire synchronisation story, and a callback straight into the textbox would be a crash waiting for the right timing.
- **Records are formatted on the thread that logged them**, not when read: a message can name an object whose `repr` changes (or raises) later. `log.demo()` covers the ring-buffer wrap, the cursor, and an unformattable record.
- **`httpmin` logs every call at DEBUG** — method, scheme://host/path, status, bytes, milliseconds — deliberately WITHOUT the query string or any header, because tokens live in both. That log is how a network problem gets diagnosed without a packet capture.

## Hotkeys

| Shortcut | Action |
|----------|--------|
| Ctrl+Alt+Up | Play/Pause |
| Ctrl+Alt+Right | Next track |
| Ctrl+Alt+Left | Previous track |
| Ctrl+Alt+L | Like/unlike current song |
| Ctrl+Alt+C | Show current song notification |

**These five are the whole set.** `config.ACTIONS` is the single source: `DEFAULT_HOTKEYS`, `ENV_HOTKEYS`, both controllers and both front ends are all keyed off it, so a sixth action cannot exist half-way. Each is overridable from the `.env` as `HOTKEY_<ACTION_ID>` — `ENV_HOTKEYS` is *derived* from `ACTIONS`, so a new action is configurable the moment it is added.

A `Ctrl+Alt+W` "Wake device" row lived here until 2026-08-11 and was **fiction** — no action, no binding, no handler, nothing in any file. It described a startup `transfer_playback` that an older build did unconditionally. `SpotifyController._device()` still does the equivalent (it activates an idle device before skipping), just not on a key press. Don't re-add the row without the code.

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
  `.env` is gitignored now, and is where credentials belong; `cadence_config.txt` (also gitignored)
  holds only the session and refresh tokens. Neither is ever in git.
- **`.env` keys are matched case-insensitively and have aliases** (`DOMAIN`/`CADENCE_URL`,
  `USERNAME`/`CADENCE_USERNAME`, ...). `ENV_FIELDS` is ordered generic-first, specific-last, because a
  later entry wins — reorder it and a file carrying both keys silently resolves to the other one.
- **`env_config()` never reads `os.environ`.** `USERNAME` is a standard Windows environment variable;
  reading the process environment would make every Windows machine "configured" with its own login name.
