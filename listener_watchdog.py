import os
import subprocess
import threading
import sys
import time
import pystray
import ctypes
from PIL import Image, ImageDraw

# State
process = None
running = True

def create_tray_icon():
    # Draws a simple red circle (like a recording indicator) for the system tray
    width, height = 64, 64
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.ellipse((16, 16, 48, 48), fill=(220, 20, 60, 255), outline=(255, 255, 255, 255), width=2)
    return image

def run_subprocess():
    global process, running
    
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "background_listener.py")
    
    # Force pythonw.exe so the child listener creates absolutely no console window
    python_exe = sys.executable
    if "python.exe" in python_exe.lower() and "pythonw.exe" not in python_exe.lower():
        pythonw_path = python_exe.lower().replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw_path):
            python_exe = pythonw_path
            
    while running:
        print("[Watchdog] Starting child listener process...")
        try:
            # We don't pipe stdout because pythonw suppresses it anyway, and we don't strictly need it
            process = subprocess.Popen([python_exe, script_path], creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait()
        except Exception as e:
            print(f"[Watchdog] Error running listener: {e}")
            
        if running:
            # If the process exited gracefully (idle timeout) or crashed, give it a tiny breath, then restart it.
            time.sleep(1)

def on_quit(icon, item):
    global running, process
    print("[Watchdog] Quitting via system tray...")
    running = False
    
    if process:
        try:
            process.terminate()
        except Exception:
            pass
            
    icon.stop()
    os._exit(0)

def restart_api(icon, item):
    print("[Watchdog] Requesting elevated restart of FasterWhisperAPI...")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", "-WindowStyle Hidden -Command Restart-Service -Name FasterWhisperAPI -Force", None, 0)
    icon.notify("Reloading Config and AI Model...", "API Restarting")

def pause_api(icon, item):
    print("[Watchdog] Requesting elevated pause of FasterWhisperAPI...")
    # 0 = SW_HIDE (hides the cmd window, but UAC prompt still shows)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", "/c net stop FasterWhisperAPI", None, 0)
    icon.notify("Freeing VRAM... Waiting for Windows Service to stop.", "API Paused")

def resume_api(icon, item):
    print("[Watchdog] Requesting elevated resume of FasterWhisperAPI...")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", "/c net start FasterWhisperAPI", None, 0)
    icon.notify("Loading AI Model into VRAM...", "API Resumed")

def setup_tray():
    icon_image = create_tray_icon()
    menu = pystray.Menu(
        pystray.MenuItem('Restart API (Apply Config Changes)', restart_api),
        pystray.MenuItem('Resume API (Load Model)', resume_api),
        pystray.MenuItem('Pause API (Free VRAM)', pause_api),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit Faster-Whisper Listener', on_quit)
    )
    icon = pystray.Icon("faster_whisper_listener", icon_image, "Faster-Whisper Listener", menu)
    
    # Start the subprocess loop in a background daemon thread
    t = threading.Thread(target=run_subprocess, daemon=True)
    t.start()
    
    # Run the tray icon (this blocks the main thread)
    icon.run()

if __name__ == "__main__":
    # If the user double clicked the watchdog and it opened a console window by mistake (e.g., using python.exe)
    # the creationflags=subprocess.CREATE_NO_WINDOW in Popen will still ensure the child is invisible.
    setup_tray()
