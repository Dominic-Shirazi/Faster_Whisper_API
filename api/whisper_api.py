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
import time
import traceback

# Load configuration from .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Configuration
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small.en")
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
# Mean-mode (roast/tiktok/disses) runs on this box against the local Ollama.
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/generate")
# Base 'prompt ai' editing can run on a separate machine (e.g. AI_worker2).
# Falls back to the local Ollama if OLLAMA_EDIT_API_URL is not set.
OLLAMA_EDIT_API_URL = os.getenv("OLLAMA_EDIT_API_URL", OLLAMA_API_URL)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
OLLAMA_ROAST_MODEL = os.getenv("OLLAMA_ROAST_MODEL", "chatgpt1/qwythos-9b-claude-mythos-5-1m-abliterated:latest")
COMPUTE_TYPE = "float16"
TEMP_DIR = os.path.dirname(__file__)
API_PORT = 5000


def _port_already_serving(host: str = "127.0.0.1", port: int = API_PORT) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


# Single-instance guard for direct launches (python api/whisper_api.py,
# LAUNCHER.bat). If something already serves the port, this is a duplicate:
# refuse to start so we never squat the port with a second, possibly stale,
# instance -- the exact failure that let an old manual run shadow the service.
# The NSSM service starts via `uvicorn api.whisper_api:app`, so __name__ is not
# "__main__" there and this guard is skipped (uvicorn owns the bind). Runs before
# the model load so a duplicate exits instantly without touching the GPU.
if __name__ == "__main__" and _port_already_serving():
    print(
        f"[API] Port {API_PORT} is already serving; another instance (likely the "
        "FasterWhisperAPI service) is running. Not starting a duplicate."
    )
    raise SystemExit(1)

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
        "trigger_patterns": [
            "prompt[\\s.,]*a\\.?\\s*i\\.?",
            "promptai",
            "prompte[\\s.,]*i\\.?",
            "end\\s*prompt",
        ],
        "roast_trigger_patterns": [
            "prompt\\s*a\\.?i\\.?\\W*(?:to\\s*)?roast",
            "prompt\\W*(?:to\\s*)?roast",
            "prompt\\s*a\\.?i\\.?\\W*(?:tik\\s*tok|tick\\s*tock)",
            "prompt\\W*(?:tik\\s*tok|tick\\s*tock)",
            "prompt\\s*a\\.?i\\.?\\W*(?:some\\s*)?diss(?:es)?",
            "prompt\\W*(?:some\\s*)?diss(?:es)?",
        ],
        "insults_file_path": "insults.md",
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


def _fastlog(msg: str) -> None:
    # NSSM does not capture stdout, so write fast-path diagnostics to a file we
    # can actually read when a trigger fires but editing/roasting misbehaves.
    try:
        with open(os.path.join(TEMP_DIR, "fastpath.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def strip_think(text: str) -> str:
    # Reasoning models (e.g. qwythos) emit a <think>...</think> block; the real
    # reply is whatever follows the last </think>. Sometimes only the closing tag
    # is present. Keep just the final answer so we never paste the reasoning.
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].strip()
    if "<think>" in text:
        return text.split("<think>", 1)[0].strip()
    return text


def sanitize_social_output(text: str) -> str:
    # Mean-mode output should be paste-ready for a social reply. The model
    # ignores the formatting rules often, so enforce them deterministically.
    # 1. No em/en dashes -> comma (then collapse any doubled commas that makes).
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r",\s*,", ",", text)
    # 2. Strip hashtags and emoji. Voice dictation never produces these, so any
    #    in the output were invented by the model against the "no hashtags/emoji"
    #    rule. Covers emoji planes, misc symbols/dingbats, flags, arrows, plus
    #    the joiner/variation-selector glue that would otherwise be orphaned.
    text = re.sub(r"#[\w-]+", "", text)
    text = re.sub(
        "[\U0001F000-\U0001FAFF"  # emoji planes (symbols, faces, etc.)
        "\U00002600-\U000027BF"   # misc symbols + dingbats
        "\U0001F1E6-\U0001F1FF"   # regional-indicator flag letters
        "\U00002190-\U000021FF"   # arrows
        "\U00002B00-\U00002BFF"   # misc symbols and arrows
        "\U0000FE0F\U0000200D]",   # variation selector + zero-width joiner
        "",
        text,
    )
    # 3. Tidy whitespace left behind by the removals.
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text


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
            print("[API] Roast/TikTok Trigger word found, processing via Ollama...")
            insults = read_insults(CONFIG.get("insults_file_path", "insults.md"))
            system_prompt = (
                "You are the ghostwriter for a witty, condescending person who is "
                "firing back at someone online (often a reply to a TikTok comment or "
                "a social media post). You are given their unstructured, dictated "
                "brain-dump. Turn it into the reply they would actually post.\n\n"
                "Do this:\n"
                "1. Figure out the reasoning failure the OTHER person is making (are "
                "they confidently wrong, ignoring evidence, faking expertise, just "
                "slow, closed-minded?). The user's brain-dump tells you what the "
                "other person got wrong; that flaw is your target.\n"
                "2. Reorganize the user's scattered thoughts into a tight, clear, "
                "succinct reply. Keep every point they actually made; cut the "
                "rambling and filler.\n"
                "3. Mirror the USER'S own voice and speech pattern from the "
                "brain-dump (their slang, rhythm, and casualness). You are writing "
                "AS the user, not to them. Do not sound like an AI.\n"
                "4. Weave in at least one insult from the category below that "
                "matches the other person's specific reasoning failure (more for a "
                "longer reply, where it fits). You may reword one or invent a "
                "similar one in the same spirit so it flows naturally. Each should "
                "feel earned, not bolted on.\n\n"
                "Hard formatting rules:\n"
                "- Plain text only. No markdown, no bullet points, no headings.\n"
                "- NEVER use em-dashes (—). Use commas, periods, or ellipses.\n"
                "- No hashtags and no emoji unless the user used them first.\n"
                "- Keep it short enough for a social media reply.\n"
                "- Output ONLY the finished reply. No preamble, no quotes around it, "
                "no explanation of what you did.\n\n"
                "Example brain-dump: 'the bible isn't real, if it was we'd expect "
                "talking snakes and burning bushes to talk too'\n"
                "Example reply: 'The bible isn't real. If it was, we'd expect the "
                "talking snakes and burning bushes to still be talking, right? Look, "
                "I could explain it further, but I left my crayons at home.'\n\n"
                f"Insults, grouped by the reasoning failure they target:\n{insults}"
            )
            try:
                response = requests.post(
                    OLLAMA_API_URL,
                    json={
                        "model": OLLAMA_ROAST_MODEL,
                        "prompt": text,
                        "system": system_prompt,
                        "stream": False,
                    },
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()
                reply = strip_think(data.get("response", text).strip())
                return sanitize_social_output(reply)
            except Exception as e:
                _fastlog(f"ROAST FAIL @ {OLLAMA_API_URL}: {e!r}\n{traceback.format_exc()}")
                print(f"[API] Ollama Roast Fast-Path error: {e}")
                return text

    # Scan for Triggers
    trigger_list = CONFIG.get("trigger_patterns", [])
    if trigger_list:
        trigger_pattern = r"\b(" + "|".join(trigger_list) + r")\b"
        if re.search(trigger_pattern, text, flags=re.IGNORECASE):
            print(f"[API] Trigger word found, processing via Ollama ({OLLAMA_MODEL} @ {OLLAMA_EDIT_API_URL})...")
            _fastlog(f"EDIT trigger fired -> POST {OLLAMA_EDIT_API_URL} model={OLLAMA_MODEL} | text={text!r}")
            try:
                response = requests.post(
                    OLLAMA_EDIT_API_URL,
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
                edited = data.get("response", text).strip()
                _fastlog(f"EDIT ok <- {len(edited)} chars")
                return edited
            except Exception as e:
                _fastlog(f"EDIT FAIL @ {OLLAMA_EDIT_API_URL}: {e!r}\n{traceback.format_exc()}")
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
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
