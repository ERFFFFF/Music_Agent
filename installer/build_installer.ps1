# Music Agent - Build & Installer Script
# Builds the PyInstaller exe, then compiles the Inno Setup installer.
# Run from the repo root: .\installer\build_installer.ps1
#
# Prerequisites:
#   - Python + pip install -r requirements.txt
#   - PyInstaller (included in requirements.txt)
#   - Inno Setup 6 (https://jrsoftware.org/isdl.php)

$ErrorActionPreference = "Stop"

# Ensure we're running from the repo root
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Step 1: Clean previous build
Write-Host "Cleaning previous build..." -ForegroundColor Cyan
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build

# Step 2: PyInstaller build
#
# Flags, not a checked-in .spec. The old "Music Agent.spec" carried datas=[('.\.env', '.')] from when
# the installer shipped Spotify credentials: every build copied THIS machine's .env into _internal\,
# and [Files] then shipped it to every user who ran the installer. The app asks for credentials at
# first launch now (config.py), so nothing secret belongs in the bundle. PyInstaller rewrites the
# .spec from these flags on every run, so there is no stale file left to re-poison the build.
Write-Host "Building with PyInstaller..." -ForegroundColor Cyan
py -m PyInstaller `
    --onedir `
    --noconsole `
    --noconfirm `
    --name "Music Agent" `
    --icon "poulet.ico" `
    --add-data "poulet.ico;." `
    --hidden-import "pystray._win32" `
    main.py
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

# Also check scoop
$ScoopPath = & { scoop prefix inno-setup 2>$null } 2>$null
if ($ScoopPath) {
    $InnoPaths = @("$ScoopPath\ISCC.exe") + $InnoPaths
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
& $ISCC installer\installer.iss
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed."
    exit 1
}

Write-Host ""
Write-Host "Installer created: installer_output\MusicAgentSetup.exe" -ForegroundColor Green
