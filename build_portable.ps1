# Portable build: ONE exe, no installer, no .env.
#
# Cadence mode needs no build-time secret (the user signs in at launch), which is what makes a single
# file possible at all - the installer only ever existed to write Spotify credentials next to the exe.
# ASCII only, deliberately: this file has no BOM, so Windows PowerShell 5.1 reads it as cp1252 and a
# UTF-8 em dash decodes to a curly quote, which PS accepts as a string delimiter and the parse dies.
# Settings and the saved session land beside the exe when that folder is writable (see config.data_dir),
# so the whole thing runs from a USB stick and keeps its hotkeys.
#
#   .\build_portable.ps1          ->  dist\MusicAgent_portable.exe   tray app
#   .\build_portable.ps1 -Cli     ->  dist\MusicAgent_cli.exe        console app, no GUI libraries
#
# -Cli installs requirements.txt only (requests + keyboard). customtkinter, pystray and Pillow are
# never downloaded and never bundled: nothing in cli.py's import graph reaches them.
#
# Spotify mode still works in both builds - the app asks for the Client ID + Secret on first launch and
# keeps them in the same cadence_config.txt, so there is nothing to bundle either way.

param([switch]$Cli)

$ErrorActionPreference = "Stop"

# PyInstaller is pinned here rather than in a requirements file because this script is the only thing
# that ever uses it: a build tool, not a dependency of the app.
$PyInstaller = "pyinstaller==6.22.0"

if ($Cli) {
    python -m pip install -r requirements.txt $PyInstaller
    pyinstaller `
        --onefile `
        --console `
        --name "MusicAgent_cli" `
        --icon "poulet.ico" `
        cli.py

    Write-Host ""
    Write-Host "Built dist\MusicAgent_cli.exe - run '.\MusicAgent_cli.exe setup' to get started." -ForegroundColor Green
} else {
    python -m pip install -r requirements-gui.txt $PyInstaller
    # --onefile: everything in one file. --noconsole: it's a tray app, a console window would just sit
    # there. The icon is added as data too, because find_icon() looks for it beside the exe AND in _MEIPASS.
    pyinstaller `
        --onefile `
        --noconsole `
        --name "MusicAgent_portable" `
        --icon "poulet.ico" `
        --add-data "poulet.ico;." `
        main.py

    Write-Host ""
    Write-Host "Built dist\MusicAgent_portable.exe - copy it anywhere and run it." -ForegroundColor Green
}
