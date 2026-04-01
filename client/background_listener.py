import keyboard
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import winsound
import threading
import time
import os
import pyperclip
import tkinter as tk
from tkinter import ttk
import requests
from dotenv import load_dotenv
import json
import sys

# Add parent directory to sys.path so we can import our human_typing module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from human_typing.human_typer import HumanTyper

# Load env file in the child too
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Configuration
SAMPLE_RATE = 44100
CHANNELS = 1
MAX_DURATION_MINS = 10
LISTENER_RESTART_INTERVAL_MINS = int(os.environ.get("LISTENER_RESTART_INTERVAL_MINS", 10))
WAV_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'temp_recording.wav')
API_URL = "http://127.0.0.1:5000/transcribe"

# Global State
recording_active = False
recording_data = []
stream = None
recording_start_time = 0
overlay = None
last_active_time = time.time()

class LoadingOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()  # Hide initially
        self.root.overrideredirect(True)  # Frameless
        self.root.attributes('-topmost', True)  # Always on top
        self.root.attributes('-alpha', 0.9)  # Slight transparency
        
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TLabel", background="#333", foreground="white", font=("Segoe UI", 12))
        style.configure("TFrame", background="#333")
        
        # Layout
        self.frame = ttk.Frame(self.root, padding=20)
        self.frame.pack(fill=tk.BOTH, expand=True)
        
        self.label = ttk.Label(self.frame, text="Transcribing...")
        self.label.pack(pady=(0, 10))
        
        self.progress = ttk.Progressbar(self.frame, mode='indeterminate', length=200)
        self.progress.pack()
        
        # Center the window
        self.center_window()

    def center_window(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        self.root.geometry(f'+{x}+{y}')

    def show(self):
        self.root.deiconify()
        self.progress.start(10)
        self.center_window()

    def hide(self):
        self.progress.stop()
        self.root.withdraw()

    def start(self):
        self.root.mainloop()

    # Thread-safe wrappers
    def show_safe(self):
        self.root.after(0, self.show)

    def hide_safe(self):
        self.root.after(0, self.hide)

    def update_label(self, new_text):
        self.label.config(text=new_text)

    def update_label_safe(self, new_text):
        self.root.after(0, lambda: self.update_label(new_text))

def beep_start():
    winsound.Beep(1000, 60)

def beep_stop():
    winsound.Beep(600, 100)

def callback(indata, frames, time_info, status):
    if recording_active:
        recording_data.append(indata.copy())

def start_recording():
    global recording_active, recording_data, stream, recording_start_time
    if recording_active:
        return
    
    print("[Listener] Starting recording...")
    beep_start()
    recording_data = []
    recording_active = True
    recording_start_time = time.time()
    
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=callback)
    stream.start()
    
    # Start timeout monitor
    threading.Thread(target=timeout_monitor, daemon=True).start()

def stop_recording():
    global recording_active, stream
    if not recording_active:
        return

    print("[Listener] Stopping recording...")
    recording_active = False
    if stream:
        stream.stop()
        stream.close()
    
    beep_stop()
    
    # Start processing in a separate thread so we don't block the hotkey/callback
    threading.Thread(target=save_and_transcribe, daemon=True).start()

def timeout_monitor():
    while recording_active:
        elapsed_mins = (time.time() - recording_start_time) / 60
        if elapsed_mins >= MAX_DURATION_MINS:
            print("[Listener] Timeout reached. Stopping.")
            stop_recording()
            break
        time.sleep(1)

def idle_monitor():
    while True:
        time.sleep(10)
        if not recording_active:
            idle_mins = (time.time() - last_active_time) / 60.0
            if idle_mins >= LISTENER_RESTART_INTERVAL_MINS:
                print(f"[Listener] Idle for {LISTENER_RESTART_INTERVAL_MINS} minutes. Exiting to allow watchdog to restart.")
                os._exit(0)

def save_and_transcribe():
    global recording_data
    
    # Show overlay
    if overlay:
        overlay.show_safe()

    try:
        if not recording_data:
            print("[Listener] No data recorded.")
            return

        print("[Listener] Saving WAV...")
        audio = np.concatenate(recording_data, axis=0)
        os.makedirs(os.path.dirname(WAV_OUTPUT_PATH), exist_ok=True)
        wav.write(WAV_OUTPUT_PATH, SAMPLE_RATE, np.int16(audio * 32767))
        
        transcribe_and_paste()
    finally:
        # Hide overlay
        if overlay:
            overlay.hide_safe()

def transcribe_and_paste():
    print("[Listener] Sending to API...")
    try:
        with open(WAV_OUTPUT_PATH, 'rb') as f:
            files = {'file': (os.path.basename(WAV_OUTPUT_PATH), f, 'audio/wav')}
            response = requests.post(API_URL, files=files, timeout=60)
            response.raise_for_status()
            
            data = response.json()
            text = data.get("text", "").strip()
        
        print(f"[Listener] Transcribed: {text}")
        
        if text:
            # Read settings to determine typing mode
            mode = "paste"
            settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
            if os.path.exists(settings_path):
                with open(settings_path, 'r') as f:
                    try:
                        mode = json.load(f).get("typing_mode", "paste")
                    except json.JSONDecodeError:
                        pass
                        
            if mode == "type":
                print("[Listener] Human Typing mode...")
                
                # Always copy to clipboard as a backup
                pyperclip.copy(text)

                time.sleep(0.1)
                keyboard.send('backspace, backspace')
                time.sleep(0.1)
                
                typer = HumanTyper()
                typer.type(text)
                print("[Listener] Typed organically.")
            else:
                # Copy to clipboard
                pyperclip.copy(text)
                
                # Paste
                time.sleep(0.1)
                keyboard.send('backspace, backspace')
                time.sleep(0.1)
                keyboard.send('ctrl+v')
                print("[Listener] Pasted immediately.")
        else:
            if overlay:
                overlay.update_label_safe("No speech detected.")
                time.sleep(2)
            
    except Exception as e:
        error_msg = f"API Error: {str(e)}"
        print(f"[Listener] {error_msg}")
        if overlay:
            overlay.update_label_safe(error_msg)
            time.sleep(3)

def toggle_recording():
    global last_active_time
    last_active_time = time.time()
    if recording_active:
        stop_recording()
    else:
        start_recording()

if __name__ == "__main__":
    # Startup sound (Rising tone)
    winsound.Beep(500, 100)
    winsound.Beep(800, 100)

    # Initialize overlay
    overlay = LoadingOverlay()
    
    # Hotkey
    print(f"[Listener] Press ` (backtick) to start/stop recording (Max {MAX_DURATION_MINS} mins).")
    keyboard.add_hotkey('`', toggle_recording)
    
    # Start robust idle monitor
    threading.Thread(target=idle_monitor, daemon=True).start()
    
    # Start GUI loop (replaces keyboard.wait)
    print("[Listener] Running...")
    overlay.start()
