
# Music Control Agent

A lightweight Spotify agent that enables you to control your Spotify playback with customizable keybinds. Easily skip tracks, pause, play, like, unlike, or go back to the previous song with simple keyboard shortcuts.

## 🌟 Project Goal

Create an agent that provides seamless control over Spotify playback using keybinds.

## 🎧 Features

- **Skip to the Next Song**: Quickly move to the next track in your playlist.
- **Pause/Play**: Toggle between pausing and playing the current track.
- **Like/Unlike Songs**: Instantly like or unlike the currently playing song.
- **Previous Song**: Return to the previous track with a simple keypress.

## 🔗 Useful Links

- **Spotify Developer Portal**: [Spotify Dashboard](https://developer.spotify.com/dashboard/)

## 🚀 Installation

### 1. Install Required Packages

Make sure you have all the necessary packages by running:

```bash
pip install -r requirements.txt
```

### 2. Create and configure the config.json file

#### config.json file

```json
{
  "spotify": {
    "client_id": "your_client_id_here",
    "client_secret": "your_client_secret_here",
    "redirect_uri": "your_callback_url_here",
    "device_name": "your_device_name_here"
  }
}
```

### 3. Build the Executable

#### For Command Prompt (CMD)

To remove old files and create a new executable:

```cmd
rmdir /s /q dist && rmdir /s /q build && del /q main.spec && pyinstaller --onefile --noconsole --icon=poulet.ico --add-data=".\config.json;." --name "Spotify Agent" main.py && echo Script completed. Please manually create a shortcut in the Startup folder.
```

#### For PowerShell

If you're using PowerShell, use the following command inside the repository folder :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, main.spec; pyinstaller --onefile --noconsole --icon=poulet.ico --add-data=".\config.json;." --name "Music Agent" main.py; Copy-Item -Path ".\dist\Music Agent.exe" -Destination "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\"
```

Or this one to not put eh exe inside the startup folder :

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist; Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build; Remove-Item -Force -ErrorAction SilentlyContinue main.spec; pyinstaller --onefile --noconsole --icon=poulet.ico --add-data=".\config.json;." --name "Music Agent" main.py
```

## 🛠️ Useful Commands

- **Terminate All Running Instances of the Agent:**

   If you need to close all running instances of the Spotify Agent, use the following command in PowerShell:

   ```powershell
   Stop-Process -Name "Music Agent" -Force
   ```
