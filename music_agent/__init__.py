"""Music Agent — global hotkeys for whatever is playing your music.

Two modes, one set of actions (see `config.ACTIONS`):

  cadence  a self-hosted Cadence account; the command is performed by an open browser tab
  spotify  this machine's Spotify Connect device, via the Web API

Two front ends over the same core:

    python -m music_agent        the CLI and the headless hotkey agent  (music_agent.cli)
    python -m music_agent.ui     the system-tray app                    (music_agent.ui.tray)

Layers, top to bottom -- imports only ever point downwards:

    ui/          tray window, settings window, sign-in window   (needs customtkinter, pystray, PIL)
    cli.py       the same actions from a terminal               (needs nothing)
    backends/    cadence.py, spotify.py -- one controller each, identical five-method contract
    config.py    the .env, cadence_config.txt, DPAPI, the proxy
    net/         httpmin (urllib) and winhttp (Windows stack); one Response type
    win32/       hotkeys.py -- RegisterHotKey. Windows-only, imported lazily by whoever needs it.
    log.py       the in-memory log every front end shows

The CLI half has NO third-party dependencies at all; only ui/ does.
"""

__version__ = "4.1.0"
