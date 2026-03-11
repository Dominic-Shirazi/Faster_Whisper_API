# Faster Whisper API & Background Listener

**Why I built this:** I got sick of poor transcription services, and I was tired of manually dictating punctuation. Worse, watching words appear on-screen in real-time broke my train of thought, especially when a word was mistranscribed.

I just wanted a tool where I could hit a button, "word vomit" my thoughts into a text field, and hit Enter—without worrying about whether it picked up what I was saying. This tool is designed to be completely invisible until it drops the finished, perfectly punctuated text into your clipboard.

Running the `small` Whisper model on CUDA (using ~0.6GB of VRAM) with a cheap $12 used Blue microphone off eBay, this thing is _dang near perfect_. It handles long pauses and auto-punctuation with near-zero issues. For how often I use it, it is worth every megabyte of VRAM.Easily start/stop the service from a system tray icon (bottom right corner by your clock)

## Features

- **Local API**: Fast transcription using CTranslate2 and ONNX.
- **Background Listener**: Use the backtick (`` ` ``) key to record audio and paste transcription directly into any app. (top left of keyboard, directly under the Esc key)
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

## Usage Modes

This application can run in two different ways depending on your preference.

### Option 1: Foreground Terminal Mode (Simple, but visible)

By default, the system runs as visible applications.

- **What this means:** When you double-click `LAUNCHER.bat`, a black command prompt window opens and stays on your taskbar.
- **The downside:** Every time you open the terminal, you have to wait 10-20 seconds for the massive AI model to load into your graphics card (VRAM). If you close the black window, the transcription stops working.
- **How to use:** Just open your `.env` file and make sure `API_VISIBLE=true` is set. Then double-click `LAUNCHER.bat`.

### Option 2: Background Windows Service ("Always-On" & Invisible)

This is the recommended way to use the app for a seamless experience.

- **Why do I want this?** The AI model is _always hot_ and waiting in your memory. When you hit the keyboard shortcut to record, it transcribes instantly. There are no ugly black terminal windows cluttering your taskbar. It also auto-restarts itself safely in the background to prevent your PC from slowing down over the weeks.
- **What happens if I don't use this?** You'll just have to use "Option 1". You must keep a black terminal window open at all times, and wait for the AI to load every time you start it. You don't need to understand how the service works, just that it hides everything and makes it instantly available 24/7.
- **How to install:**
  1. Open your `Faster_Whisper_API` folder in Windows Explorer.
  2. Find the file named `install_service.ps1`.
  3. **Right-click** `install_service.ps1` and select **"Run with PowerShell"**.
     - _(If it asks for Administrator permissions or says "Do you want to allow this app to make changes", click **Yes**)._
     - _(Alternative: Click the Start button, type "PowerShell", right-click it and choose "Run as Administrator". Then type `cd C:\Users\persi\Documents\Faster_Whisper_API` and hit enter, then type `.\install_service.ps1` and hit enter)._
  4. Follow the prompts in the blue window until it says "Service installed successfully".
  5. Open your `.env` file (notepad is fine) and change `API_VISIBLE=true` to `API_VISIBLE=false`. Save it.
  6. Double-click `LAUNCHER.bat`. From now on, you will only see a tiny red circle in your System Tray (by the clock), which manages the invisible background listener.
  7. _(Optional but Highly Recommended)_ To make the tiny red circle listener start every time you log into your PC, open your `Faster_Whisper_API` folder, right-click `install_listener_startup.ps1`, and hit **"Run with PowerShell"**. Now the entire system is 100% hands-off!

---

## How to Transcribe

Once the system is running (either via Option 1 or Option 2):

- **Action**: Press " ` " (backtick) to start recording. Press it again to stop. (That button below the "Esc" key on your keyboard that you never use)
- **Result**: The audio is sent to the local API, transcribed, copied to your clipboard, and automatically pasted wherever your cursor currently is (after automatically hitting backspace twice to delete the two backticks you used to start/stop the recording).

---

## Windows Integration

### 1. Right-Click Context Menu

To add "Transcribe to Clipboard" to your right-click menu for audio files, simply run the setup script as Administrator:

```powershell
.\install_context_menu.ps1
```

_Note: Ensure you have run the Python setup steps above first, as this relies on the virtual environment's pythonw.exe._

## Project Files

- `whisper_api.py`: The FastAPI backend server.
- `background_listener.py`: The hotkey monitoring tool with a "Transcribing..." overlay.
- `transcribe_file.py`: Helper script for the right-click context menu.
- `LAUNCHER.bat`: Primary launcher to switch between foreground/background modes.
- `install_service.ps1`: Automated installer to wrap the API as a Windows NSSM service.
- `install_context_menu.ps1`: Automated installer for the Explorer right-click integration.
- `.env`: Configuration settings for the model, device, and service behaviors.
- `.gitignore`: Configured to ignore virtual environments and common AI agent files (`agent.md`, `claude.md`, etc.).
