from fastapi import FastAPI, UploadFile, File, HTTPException
from faster_whisper import WhisperModel
import os
import shutil
import uuid
import uvicorn
from dotenv import load_dotenv
import re
import requests
import json
# Load configuration from .env file
load_dotenv()

# Configuration
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small.en")
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = "float16"
TEMP_DIR = os.path.dirname(__file__)

app = FastAPI(title="Faster Whisper API")

# Load model globally (Always Hot)
print(f"[API] Loading {MODEL_SIZE} whisper model on {DEVICE}...")
try:
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print("[API] Model loaded and ready.")
except Exception as e:
    print(f"[API] Error loading model: {e}")
    model = None


def load_config():
    config_path = os.path.join(TEMP_DIR, "processing_config.json")
    example_path = os.path.join(TEMP_DIR, "processing_config.example.json")
    
    for path in [config_path, example_path]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[API] Error loading config from {path}: {e}")
                
    return {
        "name_corrections": {"Leslie": "Lesley", "Emma": "Ame"},
        "trigger_patterns": ["prompt\\s*a\\.?i\\.?", "promptai", "end\\s*prompt"]
    }

CONFIG = load_config()

def process_transcribed_text(text: str) -> str:
    # Auto-Correct Names
    for old_name, new_name in CONFIG.get("name_corrections", {}).items():
        text = re.sub(rf"\b{old_name}\b", new_name, text, flags=re.IGNORECASE)

    # Scan for Triggers
    trigger_list = CONFIG.get("trigger_patterns", [])
    if trigger_list:
        trigger_pattern = r"\b(" + "|".join(trigger_list) + r")\b"
        if re.search(trigger_pattern, text, flags=re.IGNORECASE):
            print("[API] Trigger word found, processing via Ollama (gpt-oss:20b)...")
        try:
            response = requests.post(
                "http://192.168.0.201:11434/api/generate",
                json={
                    "model": "gpt-oss:20b",
                    "prompt": text,
                    "system": "You are the personal copy editor for Dominic Shirazi. Clean up the following dictated text for clarity and flow. Fix transcription errors and remove filler words. Do not use bullet points. If the text contains 'prompt ai' followed by instructions, prioritize those instructions. Return ONLY the corrected text, without any additional commentary or explanation unless it's asked for by the user.",
                    "stream": False,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", text).strip()
        except Exception as e:
            print(f"[API] Ollama Fast-Path error: {e}")
            return text

    # No trigger found, return text with only the name corrections
    return text


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    # Generate a unique filename to avoid collisions
    file_id = str(uuid.uuid4())
    temp_path = os.path.join(TEMP_DIR, f"{file_id}_{file.filename}")

    try:
        # Save uploaded file
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Transcribe
        segments, info = model.transcribe(temp_path, beam_size=5)
        text = "".join([segment.text for segment in segments]).strip()

        # Fast-Path Processing Layer
        text = process_transcribed_text(text)

        return {"text": text, "language": info.language, "duration": info.duration}
    except Exception as e:
        print(f"[API] Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_SIZE, "device": DEVICE}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
