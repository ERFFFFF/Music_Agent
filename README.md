
# Tags

![Python](https://img.shields.io/badge/python-3.10.5-blue.svg)
![PyPI version](https://badge.fury.io/py/pip.svg)

# Music Control Agent

A lightweight Spotify agent that enables you to control your Spotify playback with customizable keybinds. Easily skip tracks, pause, play, like, unlike, or go back to the previous song with simple keyboard shortcuts.

## 🌟 Project Goal

Create an agent that provides seamless control over Spotify playback using keybinds.

## 🎧 Features

- **Skip to the Next Song**: Quickly move to the next track in your playlist.
- **Pause/Play**: Toggle between pausing and playing the current track.
- **Like/Unlike Songs**: Instantly like or unlike the currently playing song.
- **Previous Song**: Return to the previous track with a simple keypress.
- **Wake Device**: Transfer playback to an inactive Spotify Connect device (the Spotify client must be running on the target device).

## 🔗 Useful Links

- **Spotify Developer Portal**: [Spotify Dashboard](https://developer.spotify.com/dashboard/)
- **Inno Setup**: [Download](https://jrsoftware.org/isdl.php) (only needed to build the installer)

## 🚀 Installation (for users)

Download `MusicAgentSetup.exe` from the [Releases](https://github.com/ERFFFFF/Music_Agent/releases) page and run it.

The installer will:

1. Ask where to install the app
2. Ask for your Spotify **Client ID** and **Client Secret** (see [How to get credentials](#how-to-get-spotify-credentials) below)
3. Optionally add the app to Windows startup
4. Create Start Menu shortcuts and an uninstaller

On first launch, a browser window opens once for Spotify authorization. After that, the app runs silently in the background.

### How to get Spotify credentials

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/)
2. Create a new app
3. In your app's **Settings**, copy the **Client ID** and **Client Secret**
4. Add `http://127.0.0.1:8888/callback` as a **Redirect URI** in the app settings

The device is auto-discovered at startup — the agent picks the first available Spotify Connect device. If no device is found, it retries every 5 minutes until one appears.

## 🔧 Development Setup (for contributors)

### 1. Install Required Packages

```bash
pip install -r requirements.txt
```

### 2. Create and configure the .env file

Copy the template and fill in your values:

```bash
cp .env.template .env
```

#### .env.template

```env
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

#### git-crypt

The `.env` file is encrypted via [git-crypt](https://github.com/AGWA/git-crypt) so secrets are safe in the repository. If you are a collaborator, unlock with:

```bash
git-crypt unlock
```

If you don't have the key, create your own `.env` from the `.env.template` above.

### 3. Build the Executable

#### Full build (first time or after code changes)

Builds the exe from scratch, creates a shortcut in the Windows Startup folder so the agent launches on login, and registers the app ID for notifications.

Run in PowerShell inside the repository folder :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, main.spec, "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Music Agent.lnk"; pyinstaller --noconsole --icon=poulet.ico --add-data ".\.env;." --name "Music Agent" main.py; $ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Music Agent.lnk"); $sc.TargetPath = (Resolve-Path ".\dist\Music Agent\Music Agent.exe").Path; $sc.WorkingDirectory = (Resolve-Path ".\dist\Music Agent").Path; $sc.Save(); New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
```

#### Quick rebuild (using the spec file)

If you already ran the full build once and just need to rebuild after a small change, you can reuse the existing `Music Agent.spec` file. It already contains all the build settings (icon, bundled `.env`, no console, etc.) so you don't need to pass them again :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build; pyinstaller "Music Agent.spec"
```

The output is at `dist\Music Agent\Music Agent.exe`. The Startup shortcut still points to it, so no need to recreate it.

#### Build only (without startup shortcut)

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, main.spec; pyinstaller --noconsole --icon=poulet.ico --add-data ".\.env;." --name "Music Agent" main.py; New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
```

### 4. Build the Installer

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php) to be installed.

#### Using the build script (recommended)

Builds the exe with PyInstaller then compiles the installer in one step:

```powershell
.\installer\build_installer.ps1
```

Output: `installer_output\MusicAgentSetup.exe`

#### Manual steps

```powershell
# Step 1: Build with PyInstaller
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build
pyinstaller "Music Agent.spec"

# Step 2: Compile installer (adjust path to your Inno Setup install)
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss
```

## 🛠️ Useful Commands

- **Terminate All Running Instances of the Agent:**

   If you need to close all running instances of the Spotify Agent, use the following command in PowerShell:

   ```powershell
   Stop-Process -Name "Music Agent" -Force
   ```
