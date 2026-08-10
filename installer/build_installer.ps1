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

# Step 2: PyInstaller build
Write-Host "Building with PyInstaller..." -ForegroundColor Cyan
py -m PyInstaller "Music Agent.spec"
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed."
    exit 1
}
Write-Host "PyInstaller build complete." -ForegroundColor Green

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
