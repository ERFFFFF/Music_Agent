
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

## 🚀 Installation

### 1. Install Required Packages

Make sure you have all the necessary packages by running:

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
SPOTIFY_DEVICE_ID=your_device_id_here
```

#### How to get each variable

- **SPOTIFY_CLIENT_ID** and **SPOTIFY_CLIENT_SECRET**: Create a new app on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/). Once created, go to your app's **Settings** to find the Client ID and Client Secret.

- **SPOTIFY_REDIRECT_URI**: Set this to `http://127.0.0.1:8888/callback`. You must also add this exact URI in your Spotify app settings under **Redirect URIs** on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/).

- **SPOTIFY_DEVICE_ID**: Run `python discover_device_id.py` to list all your available Spotify Connect devices and their IDs. Copy the ID of the device you want to control.

#### git-crypt

The `.env` file is encrypted via [git-crypt](https://github.com/AGWA/git-crypt) so secrets are safe in the repository. If you are a collaborator, unlock with:

```bash
git-crypt unlock
```

If you don't have the key, create your own `.env` from the `.env.template` above.

### 3. Build the Executable

#### In PowerShell

Using PowerShell, type the following command inside the repository folder :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, main.spec, "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Music Agent.exe"; pyinstaller --onefile --noconsole --icon=poulet.ico --add-data ".\.env;." --name "Music Agent" main.py; Copy-Item -Path ".\dist\Music Agent.exe" -Destination "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\"; New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
```

Or this one if you do not want to put the .exe inside the startup folder :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, main.spec; pyinstaller --onefile --noconsole --icon=poulet.ico --add-data ".\.env;." --name "Music Agent" main.py; New-Item -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Force | Out-Null; Set-ItemProperty -Path "HKCU:\Software\Classes\AppUserModelIDs\com.erfffff.musicagent" -Name "(Default)" -Value "Music Agent"
```

## 🛠️ Useful Commands

- **Terminate All Running Instances of the Agent:**

   If you need to close all running instances of the Spotify Agent, use the following command in PowerShell:

   ```powershell
   Stop-Process -Name "Music Agent" -Force
   ```
