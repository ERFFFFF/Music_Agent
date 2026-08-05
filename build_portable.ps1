# Portable build: ONE exe, no installer, no .env.
#
# Cadence mode needs no build-time secret (the user signs in at launch), which is what makes a single
# file possible at all — the installer only ever existed to write Spotify credentials next to the exe.
# Settings and the saved session land beside the exe when that folder is writable (see config.data_dir),
# so the whole thing runs from a USB stick and keeps its hotkeys.
#
#   .\build_portable.ps1          ->  dist\MusicAgent.exe
#
# Spotify mode still works in this build — the app asks for the Client ID + Secret on first launch and
# keeps them in the same cadence_config.txt, so there is nothing to bundle either way.

$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt

# --onefile: everything in one file. --noconsole: it's a tray app, a console window would just sit
# there. The icon is added as data too, because find_icon() looks for it beside the exe AND in _MEIPASS.
pyinstaller `
    --onefile `
    --noconsole `
    --name "MusicAgent" `
    --icon "poulet.ico" `
    --add-data "poulet.ico;." `
    main.py

Write-Host ""
Write-Host "Built dist\MusicAgent.exe — copy it anywhere and run it." -ForegroundColor Green
