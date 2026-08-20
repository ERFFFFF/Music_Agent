# Music Agent - Build & Installer Script
# Builds the PyInstaller exe, then compiles the Inno Setup installer.
# Run from the repo root: .\installer\build_installer.ps1
#
# Prerequisites:
#   - Python + pip install -r requirements-gui.txt   (the installer ships the tray app)
#   - PyInstaller: pip install pyinstaller==6.22.0   (a build tool, not an app dependency)
#   - Inno Setup 6 (https://jrsoftware.org/isdl.php)

$ErrorActionPreference = "Stop"

# Ensure we're running from the repo root
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Step 1: Clean previous build
Write-Host "Cleaning previous build..." -ForegroundColor Cyan
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build

# Step 1b: the build tools. Pinned here rather than in a requirements file because this script is the
# only thing that uses PyInstaller: a build tool, not a dependency of the app.
python -m pip install -r requirements-gui.txt "pyinstaller==6.22.0"

# The version lives in ONE place, pyproject.toml, and is passed to Inno Setup below. It used to be
# typed into installer.iss as well, and the two had already drifted (4.0.0 vs 4.1.0) - so the
# installer would have announced the wrong version of itself.
$Version = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"(.+)"').Matches[0].Groups[1].Value
if (-not $Version) { Write-Error "No version in pyproject.toml"; exit 1 }
Write-Host "Building version $Version" -ForegroundColor Cyan

# Step 2: PyInstaller build
#
# Flags, not a checked-in .spec. The old "Music Agent.spec" carried datas=[('.\.env', '.')] from when
# the installer shipped Spotify credentials: every build copied THIS machine's .env into _internal\,
# and [Files] then shipped it to every user who ran the installer. The app asks for credentials at
# first launch now (config.py), so nothing secret belongs in the bundle. PyInstaller rewrites the
# .spec from these flags on every run, so there is no stale file left to re-poison the build.
# `python -m PyInstaller`, not `py -m PyInstaller`. The line above installs into whatever `python`
# is, and `py` is the LAUNCHER - on a machine with more than one interpreter it happily picks a
# different one, which then has no PyInstaller in it. That is not hypothetical: it is exactly how
# this failed on a CI runner that had 3.12 (with the packages) and 3.14 (without) side by side.
Write-Host "Building with PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller `
    --onedir `
    --noconsole `
    --noconfirm `
    --name "Music Agent" `
    --icon "poulet.ico" `
    --add-data "poulet.ico;." `
    --hidden-import "pystray._win32" `
    --paths src `
    tray_entry.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed."
    exit 1
}
Write-Host "PyInstaller build complete." -ForegroundColor Green

# Step 2b: refuse to package a secret. The .env leak above shipped in v3.0.0 unnoticed because nothing
# ever looked at what went into _internal\ - this is that look.
$Leaked = Get-ChildItem -Path "dist\Music Agent" -Recurse -Force -File |
    Where-Object { $_.Name -eq ".env" -or $_.Name -like "*.env" -or $_.Name -eq "cadence_config.txt" }
if ($Leaked) {
    Write-Error "Refusing to package - credential files in the build output: $($Leaked.FullName -join ', ')"
    exit 1
}

# Step 3: Find Inno Setup compiler
$InnoPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)

# Also check scoop, IF scoop is even installed. The redirections here used to be the whole guard, and
# they are not one: with $ErrorActionPreference = "Stop" at the top, a command that does not exist is
# a TERMINATING error, so on any machine without scoop - which is most of them, including a CI runner
# - this line killed the build right after PyInstaller had finished. Ask first.
if (Get-Command scoop -ErrorAction SilentlyContinue) {
    $ScoopPath = & scoop prefix inno-setup 2>$null
    if ($ScoopPath) {
        $InnoPaths = @("$ScoopPath\ISCC.exe") + $InnoPaths
    }
}

$ISCC = $null
foreach ($p in $InnoPaths) {
    if (Test-Path $p) {
        $ISCC = $p
        break
    }
}

if (-not $ISCC) {
    Write-Error "Inno Setup not found. Install from https://jrsoftware.org/isdl.php"
    exit 1
}

# Step 4: Compile installer
Write-Host "Compiling installer with Inno Setup..." -ForegroundColor Cyan
& $ISCC "/DMyAppVersion=$Version" installer\installer.iss
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed."
    exit 1
}

Write-Host ""
Write-Host "Installer created: installer_output\MusicAgentSetup.exe" -ForegroundColor Green
