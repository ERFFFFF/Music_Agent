"""Checks for `music_agent.config` — moved here from the module's own selftest()."""

from music_agent.config import *  # noqa: F401,F403 — the public surface under test
from music_agent.config import (  # noqa: F401 — including the private names it exercises
    ACTIONS, CONFIG_FILENAME, DEFAULTS, DEFAULT_HOTKEYS, ENC_PREFIX, ENV_FILENAME, ENV_HOTKEYS, ENV_PROXY_KEYS, SECRET_FIELDS, app_dir, apply_proxy, base64, config_path, env_config, env_path, is_configured, json, load_config, os, proxy_url, redact_url, save_config, save_spotify_credentials, urllib)
from music_agent import config


def test_config():
    """Self-check: paths, round-trip, migration, and that a corrupt file can't stop the app."""
    import tempfile
    real_app_dir = config.app_dir
    # Reading a .env is opt-in, and everything below the next heading is the CLI's behaviour. The
    # tray app's side of that promise is checked in test_tray.py, in a fresh process.
    config.use_env(True)
    with tempfile.TemporaryDirectory() as d:
        config.app_dir = lambda: d
        assert config_path() == os.path.join(d, "cadence_config.txt"), config_path()

        cfg = load_config()
        assert cfg["mode"] == "cadence" and cfg["hotkeys"] == DEFAULT_HOTKEYS
        assert cfg["cadence_session"] == "" and cfg["spotify_client_id"] == ""

        # everything in one file, including the session token and the shortcuts
        cfg["cadence_url"] = "https://cadence.example"
        cfg["cadence_session"] = "cookie-value"
        cfg["cf_access_client_id"] = "cf-id"
        cfg["cf_access_client_secret"] = "cf-secret"
        cfg["hotkeys"]["play_pause"] = "f1"
        save_spotify_credentials(cfg, "sp-id", "sp-secret")

        # ...but no credential is readable in it
        raw = open(config_path()).read()
        assert "f1" in raw and '"mode": "cadence"' in raw, "mode and hotkeys stay plaintext on purpose"
        if os.name == "nt":
            for secret in ("cookie-value", "cf-id", "cf-secret", "sp-id", "sp-secret", "cadence.example"):
                assert secret not in raw, f"{secret} is sitting in {CONFIG_FILENAME} IN PLAINTEXT"
            # Every secret that HAS a value is sealed. Counting SECRET_FIELDS instead was wrong from
            # the day spotify_refresh_token joined them: _encrypt leaves "" as "", so an unset field
            # contributes no prefix and this failed on a config that was perfectly correct.
            assert raw.count(ENC_PREFIX) == sum(1 for k in SECRET_FIELDS if cfg.get(k)), raw
        back = load_config()
        assert back["cadence_url"] == "https://cadence.example"
        assert back["cadence_session"] == "cookie-value"
        assert back["cf_access_client_id"] == "cf-id" and back["cf_access_client_secret"] == "cf-secret"
        assert back["spotify_client_id"] == "sp-id" and back["spotify_redirect_uri"].endswith("/callback")
        assert back["hotkeys"]["play_pause"] == "f1"                             # overridden
        assert back["hotkeys"]["next_track"] == DEFAULT_HOTKEYS["next_track"]     # rest defaulted

        # a refresh token belongs to the app that issued it: swapping credentials must drop it
        back["spotify_refresh_token"] = "issued-to-sp-id"
        save_spotify_credentials(back, "sp-id", "sp-secret")          # same app, keep it
        assert load_config()["spotify_refresh_token"] == "issued-to-sp-id"
        save_spotify_credentials(back, "other-id", "other-secret")    # different app, drop it
        assert load_config()["spotify_refresh_token"] == ""
        if os.name != "nt":
            assert oct(os.stat(config_path()).st_mode)[-3:] == "600", "session token must not be world-readable"

        # a blob from another Windows account reads as "not signed in", not as a broken token
        if os.name == "nt":
            with open(config_path()) as f:
                data = json.load(f)
            data["cadence_session"] = ENC_PREFIX + base64.b64encode(b"not my blob").decode()
            with open(config_path(), "w") as f:
                json.dump(data, f)
            assert load_config()["cadence_session"] == "", "an unopenable blob must not reach the app"

        # an existing plaintext config is encrypted on the next load, not left lying around
        with open(config_path(), "w") as f:
            json.dump({"mode": "cadence", "cadence_session": "old-plaintext"}, f)
        assert load_config()["cadence_session"] == "old-plaintext"
        if os.name == "nt":
            assert "old-plaintext" not in open(config_path()).read(), "upgrade must re-encrypt in place"

        # a corrupt file falls back to defaults instead of crashing the launch
        open(config_path(), "w").write("{ not json")
        assert load_config()["hotkeys"] == DEFAULT_HOTKEYS

        save_config({**DEFAULTS, "mode": "nonsense"})
        assert load_config()["mode"] == "cadence", "an unknown mode must not be trusted"

    # migration: the old two files land in the new one, untouched afterwards
    with tempfile.TemporaryDirectory() as d:
        config.app_dir = lambda: d
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({"mode": "cadence", "cadence_url": "https://old.example",
                       "cf_access_client_id": "old-cf", "hotkeys": {"next_track": "f2"}}, f)
        with open(os.path.join(d, "cadence_session.json"), "w") as f:
            json.dump({"url": "https://old.example", "session": "old-cookie"}, f)
        cfg = load_config()
        assert cfg["cadence_url"] == "https://old.example" and cfg["cadence_session"] == "old-cookie"
        assert cfg["cf_access_client_id"] == "old-cf"
        assert cfg["hotkeys"]["next_track"] == "f2"
        assert cfg["hotkeys"]["play_pause"] == DEFAULT_HOTKEYS["play_pause"], \
            "migrating one bound hotkey must not drop the defaults for the other four"
        assert is_configured(cfg), "a migrated Cadence session counts as configured"
        assert not is_configured({**DEFAULTS, "mode": "spotify"})
        save_config(cfg)
        assert os.path.isfile(os.path.join(d, "config.json")), "migration must not delete the old files"

    # ------------------------------------------------------------------ the .env is the source of truth
    with tempfile.TemporaryDirectory() as d:
        config.app_dir = lambda: d
        assert env_path() == "" and env_config() == {}, "no .env is a normal state, not an error"

        with open(os.path.join(d, ENV_FILENAME), "w", encoding="utf-8") as f:
            f.write("# a comment\n"
                    "\n"
                    "DOMAIN=music.example.com\n"                 # bare host: must gain https
                    "USERNAME=someone\n"
                    "PASSWORD=p#ss w+rd$with=signs\n"            # '#' is NOT an inline comment here
                    'SPOTIFY_CLIENT_ID="quoted-id"\n'            # one matching pair is unwrapped
                    "SPOTIFY_CLIENT_SECRET=\n"                   # blank: ignored, not "set to empty"
                    "CF_Access_Client_Id=cf-id.access\n"         # the case people actually type
                    "CF_ACCESS_CLIENT_SECRET=cf-secret\n")
        cfg = load_config()
        assert cfg["cadence_url"] == "https://music.example.com"
        assert cfg["cadence_username"] == "someone"
        assert cfg["cadence_password"] == "p#ss w+rd$with=signs", cfg["cadence_password"]
        assert cfg["spotify_client_id"] == "quoted-id"
        assert cfg["cf_access_client_id"] == "cf-id.access" and cfg["cf_access_client_secret"] == "cf-secret"
        assert cfg["spotify_client_secret"] == "", "a blank line must not be read as a value"
        assert is_configured(cfg), "server + account in the .env is enough to run unattended"

        # ...and none of it is copied into cadence_config.txt, encrypted or otherwise. One secret,
        # one file, one thing to rotate.
        cfg["cadence_session"] = "earned-cookie"
        cfg["hotkeys"]["play_pause"] = "f7"
        save_config(cfg)
        stored = json.load(open(config_path()))
        for field in ("cadence_url", "cadence_username", "cadence_password", "spotify_client_id",
                      "cf_access_client_id", "cf_access_client_secret"):
            assert not stored.get(field), f"{field} was copied out of the .env into the config file"
        assert "someone" not in open(config_path()).read()
        assert stored["cadence_session"] and stored["hotkeys"]["play_pause"] == "f7", \
            "what the app earns itself still belongs in the config file"

        # the .env still wins on the way back in, and an edit to it takes effect with no re-save
        assert load_config()["cadence_url"] == "https://music.example.com"
        with open(os.path.join(d, ENV_FILENAME), "a", encoding="utf-8") as f:
            f.write("CADENCE_URL=https://moved.example\nMODE=spotify\n")
        moved = load_config()
        assert moved["cadence_url"] == "https://moved.example", "CADENCE_URL must beat DOMAIN"
        assert moved["mode"] == "spotify" and moved["cadence_session"] == "earned-cookie"

        # ...and `mode` obeys the same rule as everything else the .env supplies: not persisted, so
        # there is no stale second copy sitting in the config file claiming otherwise.
        save_config(moved)
        assert json.load(open(config_path()))["mode"] == DEFAULTS["mode"], "an .env mode was persisted"
        assert load_config()["mode"] == "spotify", "...but the .env still drives it"

    # ------------------------------------------------------------------ shortcuts from the .env
    with tempfile.TemporaryDirectory() as d:
        config.app_dir = lambda: d
        cfg = load_config()
        cfg["hotkeys"]["next_track"] = "f2"          # a shortcut the CONFIG FILE owns
        cfg["hotkeys"]["like_unlike"] = "f3"
        save_config(cfg)

        with open(os.path.join(d, ENV_FILENAME), "w", encoding="utf-8") as f:
            f.write("HOTKEY_PLAY_PAUSE=ctrl+shift+p\nhotkey_like_unlike=ctrl+shift+k\n")  # case: free
        back = load_config()
        assert back["hotkeys"]["play_pause"] == "ctrl+shift+p", "the .env must set a shortcut"
        assert back["hotkeys"]["like_unlike"] == "ctrl+shift+k", "...and beat the saved one"
        assert back["hotkeys"]["next_track"] == "f2", "naming one shortcut must not reset the others"
        assert back["hotkeys"]["show_current"] == DEFAULT_HOTKEYS["show_current"]

        # ...and what the .env owns never lands in the config file, so deleting the line restores the
        # default rather than resurrecting whatever was last saved under it
        save_config(back)
        stored = json.load(open(config_path()))["hotkeys"]
        assert stored["play_pause"] == DEFAULT_HOTKEYS["play_pause"], stored
        assert stored["like_unlike"] == DEFAULT_HOTKEYS["like_unlike"], stored
        assert stored["next_track"] == "f2", "a shortcut the config file owns must survive a save"
        os.remove(os.path.join(d, ENV_FILENAME))
        gone = load_config()
        assert gone["hotkeys"]["play_pause"] == DEFAULT_HOTKEYS["play_pause"]
        assert gone["hotkeys"]["like_unlike"] == DEFAULT_HOTKEYS["like_unlike"]
        assert gone["hotkeys"]["next_track"] == "f2"

        # every action has to be reachable from the .env, or one of them is silently unconfigurable
        assert set(ENV_HOTKEYS.values()) == set(ACTIONS), ENV_HOTKEYS

    # ------------------------------------------------------- proxy, for a network that mandates one
    with tempfile.TemporaryDirectory() as d:
        config.app_dir = lambda: d
        saved = {key: os.environ.get(key) for key in ENV_PROXY_KEYS}
        try:
            for key in ENV_PROXY_KEYS:
                os.environ.pop(key, None)
            assert apply_proxy(load_config()) == {}, "no proxy configured is normal"

            # Credentials are separate fields so Settings can have three boxes; they have to compose
            # into one URL, and a password with URL punctuation in it must not split that URL.
            assert proxy_url({"proxy_url": ""}) == "", "no address means no proxy, not 'http://'"
            assert proxy_url({"proxy_url": "proxy:8080"}) == "http://proxy:8080", "bare host gains a scheme"
            assert proxy_url({"proxy_url": "http://p:3128", "proxy_user": "alice"}) == \
                "http://alice@p:3128", "a user with no password is still a user"
            composed = proxy_url({"proxy_url": "p:8080", "proxy_user": "corp\\alice",
                                  "proxy_password": "p@ss:w/rd"})
            assert composed == "http://corp%5Calice:p%40ss%3Aw%2Frd@p:8080", composed
            # ...and that really is one parseable URL, with the original values back out of it
            parsed = urllib.parse.urlsplit(composed)
            assert parsed.hostname == "p" and parsed.port == 8080, parsed
            assert urllib.parse.unquote(parsed.username) == "corp\\alice"
            assert urllib.parse.unquote(parsed.password) == "p@ss:w/rd"
            # a domain\user and its password must never be readable in anything we display
            assert redact_url(composed).endswith("@p:8080") and "%2Frd" not in redact_url(composed)

            with open(os.path.join(d, ENV_FILENAME), "w") as f:
                f.write("PROXY=http://corp:8080\n")
            load_config()                       # every entry point goes through this, so it must apply
            assert os.environ["HTTP_PROXY"] == "http://corp:8080"
            assert os.environ["HTTPS_PROXY"] == "http://corp:8080", "PROXY has to cover both schemes"
            assert "NO_PROXY" not in os.environ, "PROXY is an address, not a bypass list"

            # the explicit names beat the shorthand, and NO_PROXY passes through untouched
            with open(os.path.join(d, ENV_FILENAME), "w") as f:
                f.write("PROXY=http://both:8080\nHTTPS_PROXY=http://secure:8443\nNO_PROXY=localhost\n")
            load_config()
            assert os.environ["HTTPS_PROXY"] == "http://secure:8443"
            assert os.environ["HTTP_PROXY"] == "http://both:8080"
            assert os.environ["NO_PROXY"] == "localhost"

            # ...and urllib really reads what was exported. That is the ONLY reason this works: the
            # proxy is never passed to httpmin, it is picked up by the ProxyHandler in every opener.
            assert urllib.request.getproxies().get("https") == "http://secure:8443"
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
    config.app_dir = real_app_dir
