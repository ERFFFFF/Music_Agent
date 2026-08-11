# Portable build: ONE exe of the TRAY app, no installer.
#
# Cadence mode needs no build-time secret (the user signs in at launch, or drops a .env beside the
# exe), which is what makes a single file possible at all - the installer only ever existed to write
# Spotify credentials next to the exe.
# ASCII only, deliberately: this file has no BOM, so Windows PowerShell 5.1 reads it as cp1252 and a
# UTF-8 em dash decodes to a curly quote, which PS accepts as a string delimiter and the parse dies.
# Settings and the saved session land beside the exe when that folder is writable (see config.data_dir),
# so the whole thing runs from a USB stick and keeps its hotkeys.
#
#   .\build_portable.ps1     ->  dist\MusicAgent_portable.exe
#
# THE CLI IS NOT BUILT, on purpose. It has no dependencies to bundle - you run it with
# `python -m music_agent <verb>` and it is live: edit the file, run it again, that is the whole
# loop. Freezing it would add a 10 MB artifact and a rebuild step between every change, to ship an
# interpreter the machine already has. Only the tray app earns an exe, because a tray app has to start
# from a shortcut with no console.
#
# Spotify mode still works in the build - the app asks for the Client ID + Secret on first launch (or
# reads the .env), so there is nothing to bundle either way.

$ErrorActionPreference = "Stop"

# PyInstaller is pinned here rather than in a requirements file because this script is the only thing
# that ever uses it: a build tool, not a dependency of the app.
$PyInstaller = "pyinstaller==6.22.0"

# PyInstaller needs a plain script, not a package entry point, and it must be able to import
# `music_agent` -- so the entry script sits at the project root next to the package.

python -m pip install -r requirements-gui.txt $PyInstaller
# --onefile: everything in one file. --noconsole: it's a tray app, a console window would just sit
# there. The icon is added as data too, because find_icon() looks for it beside the exe AND in _MEIPASS.
pyinstaller `
    --onefile `
    --noconsole `
    --name "MusicAgent_portable" `
    --icon "poulet.ico" `
    --add-data "poulet.ico;." `
    tray_entry.py

Write-Host ""
Write-Host "Built dist\MusicAgent_portable.exe - copy it anywhere and run it." -ForegroundColor Green
Write-Host "The CLI is not built: run 'python -m music_agent status' from the source folder." -ForegroundColor DarkGray
