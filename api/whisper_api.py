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
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Configuration
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small.en")
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
OLLAMA_ROAST_MODEL = os.getenv("OLLAMA_ROAST_MODEL", "llama2-uncensored:latest")
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

    config = {
        "name_corrections": {"Leslie": "Lesley", "Emma": "Ame"},
        "trigger_patterns": ["prompt\\s*a\\.?i\\.?", "promptai", "end\\s*prompt"],
        "roast_trigger_patterns": [
            "prompt\\s*a\\.?i\\.?\\W*(?:to\\s*)?roast",
            "prompt\\W*(?:to\\s*)?roast",
        ],
        "insults_file_path": "insults.txt",
    }

    for path in [config_path, example_path]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    config.update(loaded)
                    return config
            except Exception as e:
                print(f"[API] Error loading config from {path}: {e}")

    return config


CONFIG = load_config()


def read_insults(filepath: str) -> str:
    path = filepath
    if not os.path.isabs(path):
        # Resolve relative to the project root (one directory up from api/)
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def process_transcribed_text(text: str) -> str:
    # Auto-Correct Names
    for old_name, new_name in CONFIG.get("name_corrections", {}).items():
        text = re.sub(rf"\b{old_name}\b", new_name, text, flags=re.IGNORECASE)

    # Scan for Roast Triggers first
    roast_list = CONFIG.get("roast_trigger_patterns", [])
    if roast_list:
        roast_pattern = r"\b(" + "|".join(roast_list) + r")\b"
        if re.search(roast_pattern, text, flags=re.IGNORECASE):
            print("[API] Roast Trigger word found, processing via Ollama...")
            insults = read_insults(CONFIG.get("insults_file_path", "insults.txt"))
            system_prompt = f"You are a writing assistant for a condescending and witty person and your goal is to make their writing as condescending and witty as possible. Please help roast this person. Rewrite the prompt to be as condescending as possible and for better flow, insert supplied insults throughout the roast (at LEAST one per request, more for longer requests, where it makes sense). You can either use the provided insults or make up similar ones to be inserted into the text output ('Insults so smart they take a few minutes to realize you were insulted'). Make sure they flow well (modify the text or insult to fit better if it makes sense). Do not speak directly to the user, You are writing as the user. Example: Prompt: 'The bible isn't real, if it was we'd expect talking snakes and burning bushes to talk too' Response: 'The bible isn't real. If it was we'd expect snakes and burning bushes to talk, ok?...Look, I could explain it to you further... But I left my crayons at home.' (you can add and/or alter the text or insults, but ensure the original message is still conveyed.) \n\n provided Insults:\n{insults}"
            try:
                response = requests.post(
                    OLLAMA_API_URL,
                    json={
                        "model": OLLAMA_ROAST_MODEL,
                        "prompt": text,
                        "system": system_prompt,
                        "stream": False,
                    },
                    timeout=60,
                )
                response.raise_for_status()
                data = response.json()
                return data.get("response", text).strip()
            except Exception as e:
                print(f"[API] Ollama Roast Fast-Path error: {e}")
                return text

    # Scan for Triggers
    trigger_list = CONFIG.get("trigger_patterns", [])
    if trigger_list:
        trigger_pattern = r"\b(" + "|".join(trigger_list) + r")\b"
        if re.search(trigger_pattern, text, flags=re.IGNORECASE):
            print(f"[API] Trigger word found, processing via Ollama ({OLLAMA_MODEL})...")
            try:
                response = requests.post(
                    OLLAMA_API_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": text,
                        "system": "You are a personal copy editor. Clean up the following dictated text for clarity and flow. Fix transcription errors and remove filler words. Do not use bullet points, string text only. If the text contains 'prompt ai' followed by instructions, prioritize those instructions. Return ONLY the corrected text, without any additional commentary or explanation unless it's asked for by the user.",
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


# Added /transcribe_time_stamped endpoint to provide segment-level timestamps without breaking existing /transcribe endpoint
@app.post("/transcribe_time_stamped")
async def transcribe_time_stamped(file: UploadFile = File(...)):
    # Generate a unique filename to avoid collisions
    file_id = str(uuid.uuid4())
    temp_path = os.path.join(TEMP_DIR, f"{file_id}_{file.filename}")

    try:
        # Save uploaded file
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Transcribe
        segments_gen, info = model.transcribe(temp_path, beam_size=5)
        
        # segments is a generator, consume it to get all items
        segments = list(segments_gen)
        
        # Keep original text exactly as Faster Whisper output it
        full_text = "".join([segment.text for segment in segments]).strip()
        
        # Build segment-level data
        formatted_segments = []
        for segment in segments:
            formatted_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
            })

        # We intentionally skip process_transcribed_text() here because AI rewriting 
        # (like roasting or fixing grammar) would break the alignment with the timestamps.
        return {
            "text": full_text,
            "segments": formatted_segments,
            "language": info.language,
            "duration": info.duration
        }
    except Exception as e:
        print(f"[API] Time-stamped transcription error: {e}")
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
