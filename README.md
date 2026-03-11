# Faster Whisper API & Background Listener

A high-performance transcription system for Windows using `faster-whisper`. This project provides a local API server for transcription and a background listener that allows you to transcribe audio from your microphone or files directly to your clipboard using hotkeys and context menus.

## Features
- **Local API**: Fast transcription using CTranslate2 and ONNX.
- **Background Listener**: Use the backtick (`` ` ``) key to record audio and paste transcription directly into any app.
- **Context Menu Integration**: Right-click any audio file in Windows Explorer to "Transcribe to Clipboard".

---

## Installation

### 1. Prerequisites
- Python 3.8+
- NVIDIA GPU with CUDA (recommended) or a fast CPU.

### 2. Setup Environment
```powershell
# Create virtual environment
python -m venv .venv

# Activate environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy the default configuration file and edit it to suit your hardware.
```powershell
copy .env.example .env
```
Open `.env` to configure your `WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`, and background service settings.

---

## Usage
### Option 1: Foreground Terminal Mode
By default, the `.env` file has `API_VISIBLE=true`.
Simply double-click `LAUNCHER.bat` in the project folder. You will see both the API and Listener command prompt windows.

### Option 2: Background Windows Service ("Always-On")
If you want the AI model to stay hot in your GPU VRAM without keeping a terminal open:
1. Run `install_service.ps1` as Administrator. This downloads NSSM and configures the API to boot silently with Windows. It also schedules a restart every X hours based on your `.env`.
2. Change `API_VISIBLE=false` in your `.env` file.
3. Double-click `LAUNCHER.bat`. It will quietly verify the service is running and then launch just the Background Listener hotkey tool.

---

## Usage

### Start the System
You can start both the API server and the background listener using the provided launcher.
Simply double-click `LAUNCHER.bat` in the project folder.

- **Action**: Press `` ` `` (backtick) to start recording. Press it again to stop.
- **Result**: The audio is sent to the local API, transcribed, copied to your clipboard, and automatically pasted.

---

## Windows Integration

### 1. Right-Click Context Menu
To add "Transcribe to Clipboard" to your right-click menu for audio files, simply run the setup script as Administrator:

```powershell
.\install_context_menu.ps1
```
*Note: Ensure you have run the Python setup steps above first, as this relies on the virtual environment's pythonw.exe.*

## Project Files
- `whisper_api.py`: The FastAPI backend server.
- `background_listener.py`: The hotkey monitoring tool with a "Transcribing..." overlay.
- `transcribe_file.py`: Helper script for the right-click context menu.
- `LAUNCHER.bat`: Primary launcher to switch between foreground/background modes.
- `install_service.ps1`: Automated installer to wrap the API as a Windows NSSM service.
- `install_context_menu.ps1`: Automated installer for the Explorer right-click integration.
- `.env`: Configuration settings for the model, device, and service behaviors.
- `.gitignore`: Configured to ignore virtual environments and common AI agent files (`agent.md`, `claude.md`, etc.).
