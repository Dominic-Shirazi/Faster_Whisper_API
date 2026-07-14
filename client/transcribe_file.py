import sys
import requests
import pyperclip
import tkinter as tk
import os
import threading
import time
from dotenv import load_dotenv

# Load .env from the repo root so WHISPER_API_URL (and friends) are picked up
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

API_URL = os.environ.get("WHISPER_API_URL", "http://127.0.0.1:5000/transcribe")

def show_toast(message, duration=2000):
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.9)
    root.configure(bg="#333")
    
    label = tk.Label(root, text=message, fg="white", bg="#333", 
                    padx=20, pady=10, font=("Segoe UI", 12, "bold"))
    label.pack()
    
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    
    # Bottom right corner
    x = screen_width - width - 20
    y = screen_height - height - 60
    root.geometry(f"+{x}+{y}")
    
    root.after(duration, root.destroy)
    root.mainloop()

def transcribe_file(file_path):
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    # Non-blocking processing toast would be complex with tkinter, 
    # so we'll just start the work and show the result.
    # But let's at least show one that says we started.
    
    print(f"Transcribing {file_path}...")
    try:
        # Start a thread for the API call so we can show a "Started" toast
        result = {"text": None, "error": None}
        
        def do_work():
            try:
                with open(file_path, 'rb') as f:
                    files = {'file': (os.path.basename(file_path), f)}
                    response = requests.post(API_URL, files=files, timeout=300)
                    response.raise_for_status()
                    result["text"] = response.json().get("text", "").strip()
            except Exception as e:
                result["error"] = str(e)

        work_thread = threading.Thread(target=do_work)
        work_thread.start()
        
        # Show "Started" toast briefly
        show_toast(f"Transcribing {os.path.basename(file_path)}...", 1500)
        
        # Wait for work to finish
        while work_thread.is_alive():
            time.sleep(0.1)
            
        if result["text"]:
            pyperclip.copy(result["text"])
            show_toast("Transcribed to Clipboard!", 2500)
        elif result["error"]:
            show_toast(f"Error: {result['error']}", 4000)
        else:
            show_toast("Transcription failed: No text found.", 3000)
                
    except Exception as e:
        print(f"Error: {e}")
        show_toast(f"Error: {str(e)}", 4000)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        transcribe_file(sys.argv[1])
    else:
        print("Usage: python transcribe_file.py <path_to_file>")
