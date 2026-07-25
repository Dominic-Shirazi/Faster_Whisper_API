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

# Per-segment confidence gates. faster-whisper already returns these scores on
# every Segment; we just drop the ones the model itself distrusts. Values mirror
# OpenAI Whisper's own silence/degenerate-output defaults. Tunable via .env.
# The silence gate deliberately requires BOTH a high no-speech probability AND a
# low decode confidence, so a genuine quiet word (which carries real audio) is
# never cut -- only model-invented filler over true silence.
NO_SPEECH_THRESHOLD = float(os.getenv("NO_SPEECH_THRESHOLD", 0.6))
LOGPROB_THRESHOLD = float(os.getenv("LOGPROB_THRESHOLD", -1.0))
COMPRESSION_RATIO_THRESHOLD = float(os.getenv("COMPRESSION_RATIO_THRESHOLD", 2.4))
# Log every raw+final transcript to transcripts.log to build a real corpus of
# your own speech (trigger-catch coverage, hallucinations, edit results).
LOG_TRANSCRIPTS = os.getenv("LOG_TRANSCRIPTS", "true").lower() in ("1", "true", "yes")

# Roast (mean-mode) runs on a local reasoning model that cold-loads into VRAM
# already occupied by Whisper, so a roast fired right after a transcription is far
# slower than a warm run (~30s). Give it generous headroom or it times out and
# silently falls back to the raw text with no roast. Tune via .env.
ROAST_TIMEOUT = int(os.getenv("ROAST_TIMEOUT_SECONDS", 300))
# Roast command detector, used as a safety net: if the edit trigger fires but the
# spoken instruction is really a roast command (e.g. Whisper heard "Prompt AI roast"
# as "Prompte I roast", which the roast patterns miss), route it to the roaster.
# Anchored at the start (after an optional polite lead-in) so a real command like
# "roast this guy" matches while an incidental mention ("clean up my pot roast
# recipe") does not.
ROAST_COMMAND_RE = re.compile(
    r"^(?:(?:will|would|could|can)\s+you\s+|please\s+)*"
    r"(?:roast|tik\s*tok|tick\s*tock|(?:some\s*)?diss(?:es)?)\b",
    re.IGNORECASE,
)


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
            # 'prompt' + up to two non-letters + 'a' + up to two non-letters + 'i',
            # with 'i' optionally spelled out phonetically as 'eye'. Case-insensitive
            # (the caller passes re.IGNORECASE). Absorbs every way Whisper renders the
            # spoken trigger -- "prompt ai", "A.I.", "A-I", "a eye", "A. Eye",
            # "promptai" -- while the trailing word boundary keeps "prompt a idea" /
            # "prompt a image" from firing on the 'i' inside the next word.
            "prompt[^a-zA-Z]{0,2}a[^a-zA-Z]{0,2}(?:eye|i)",
            "prompte[\\s.,\\-]*i\\.?",
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


def _segment_is_reliable(seg) -> bool:
    """Return False for segments the model itself flags as junk.

    Two gates, both from scores faster-whisper already returns:
      1. Silence filler: no_speech_prob high AND avg_logprob low -- the model
         thinks this window is silence and isn't confident in the words. This
         is the exact signature of the "Thank you." / "I don't know." filler
         Whisper emits over a long pause. Requiring BOTH conditions keeps a
         real, quietly-spoken word (which has audio behind it, so a low
         no_speech_prob) from being dropped.
      2. Decode loop: compression_ratio above threshold means the text is so
         repetitive it compresses hard ("I'm I'm I'm I'm..."), i.e. the decoder
         got stuck. Normal speech stays well under 2.4.
    """
    if seg.no_speech_prob > NO_SPEECH_THRESHOLD and seg.avg_logprob < LOGPROB_THRESHOLD:
        return False
    if seg.compression_ratio > COMPRESSION_RATIO_THRESHOLD:
        return False
    return True


def _confidence_summary(segments: list) -> dict:
    """Collapse a list of segments into one comparable confidence score.

    faster-whisper reports avg_logprob per segment (higher = more confident,
    typically ~-0.15 on clean near-field speech, dipping past ~-0.7 as noise
    degrades the decode). We weight by segment duration so a long clip and a
    short one compare fairly, and also surface the single worst segment and the
    mean no-speech probability. This is the score the adaptive dual-pass will
    compare between the raw and denoised passes; logging it now, before that
    feature exists, is how we discover where the quiet-room vs. noisy cutoff
    actually falls on real speech instead of guessing.
    """
    if not segments:
        return {"wlogp": 0.0, "minlogp": 0.0, "mean_nsp": 0.0, "n": 0}
    total_dur = 0.0
    weighted = 0.0
    for s in segments:
        dur = max(s.end - s.start, 1e-3)
        weighted += s.avg_logprob * dur
        total_dur += dur
    return {
        "wlogp": weighted / total_dur if total_dur else 0.0,
        "minlogp": min(s.avg_logprob for s in segments),
        "mean_nsp": sum(s.no_speech_prob for s in segments) / len(segments),
        "n": len(segments),
    }


def _log_transcript(raw_text: str, final_text: str, info, kept: list, dropped: list) -> None:
    """Append one record per transcription to transcripts.log for corpus building.

    Logs the raw Whisper output, the final post-processed text (so trigger/edit
    effects are visible), a per-clip confidence summary (so we can see the real
    distribution of decode confidence on actual speech), and any segments the
    confidence gate removed with their scores (so the thresholds can be audited
    and tuned). Best-effort; never raises.
    """
    if not LOG_TRANSCRIPTS:
        return
    try:
        path = os.path.join(TEMP_DIR, "transcripts.log")
        with open(path, "a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(
                f"--- {ts} | lang={info.language} dur={info.duration:.1f}s "
                f"dropped={len(dropped)} ---\n"
            )
            c = _confidence_summary(kept)
            f.write(
                f"CONF : wlogp={c['wlogp']:.3f} minlogp={c['minlogp']:.3f} "
                f"mean_nsp={c['mean_nsp']:.2f} segs={c['n']}\n"
            )
            f.write(f"RAW  : {raw_text!r}\n")
            if final_text != raw_text:
                f.write(f"FINAL: {final_text!r}\n")
            for d in dropped:
                f.write(
                    f"DROP : nsp={d.no_speech_prob:.2f} logp={d.avg_logprob:.2f} "
                    f"cr={d.compression_ratio:.2f} {d.text.strip()!r}\n"
                )
    except Exception:
        pass


def _split_on_trigger(text: str, patterns: list) -> tuple:
    """Split dictated text at the 'prompt AI' trigger into (content, instruction).

    Everything before the trigger is the material to work on; everything after it
    is the spoken instruction for the model. Doing this in code (instead of asking
    the model to ignore the trigger) is what stops the trigger phrase and the
    directions from leaking back into the output -- the model only ever receives
    them as separate, labeled fields, never as part of the content it echoes.

    Returns (content, instruction), each with the trigger removed and surrounding
    punctuation trimmed. Either may be empty.
    """
    # Split on the 'prompt AI' family only, never the 'end prompt' terminator.
    edit_patterns = [p for p in patterns if "end" not in p.lower()]
    if not edit_patterns:
        return text.strip(), ""
    pat = re.compile(r"\b(" + "|".join(edit_patterns) + r")\b", flags=re.IGNORECASE)
    m = pat.search(text)
    if not m:
        return text.strip(), ""
    content = text[: m.start()]
    instruction = text[m.end():]
    # A spoken "end prompt" terminator, if present, is not part of the instruction.
    instruction = re.sub(r"\bend\s*prompt\b", " ", instruction, flags=re.IGNORECASE)
    strip_chars = " \t\n,.;:!?-"
    return content.strip(strip_chars), instruction.strip(strip_chars)


def _do_roast(brain_dump: str) -> str:
    """Rewrite a dictated brain-dump as a witty social-media clapback, weaving in a
    matching line from the insults bank. Shared by the direct roast trigger and the
    edit-path safety net (an edit trigger whose instruction turns out to be a roast).
    On any failure returns the brain-dump unchanged."""
    print("[API] Roast/TikTok mode: processing via Ollama...")
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
                "prompt": brain_dump,
                "system": system_prompt,
                "stream": False,
            },
            timeout=ROAST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        reply = strip_think(data.get("response", brain_dump).strip())
        return sanitize_social_output(reply)
    except Exception as e:
        _fastlog(f"ROAST FAIL @ {OLLAMA_API_URL}: {e!r}\n{traceback.format_exc()}")
        print(f"[API] Ollama Roast Fast-Path error: {e}")
        return brain_dump


def process_transcribed_text(text: str) -> str:
    # Auto-Correct Names
    for old_name, new_name in CONFIG.get("name_corrections", {}).items():
        text = re.sub(rf"\b{old_name}\b", new_name, text, flags=re.IGNORECASE)

    # Scan for Roast Triggers first
    roast_list = CONFIG.get("roast_trigger_patterns", [])
    if roast_list:
        roast_pattern = r"\b(" + "|".join(roast_list) + r")\b"
        if re.search(roast_pattern, text, flags=re.IGNORECASE):
            # Remove the trigger phrase from the brain-dump so it can't surface in
            # the roast reply. The instruction (roast) is fixed in the system prompt.
            roast_content = re.sub(roast_pattern, " ", text, flags=re.IGNORECASE).strip(" \t\n,.;:!?-")
            return _do_roast(roast_content)

    # Scan for Triggers
    trigger_list = CONFIG.get("trigger_patterns", [])
    if trigger_list:
        trigger_pattern = r"\b(" + "|".join(trigger_list) + r")\b"
        if re.search(trigger_pattern, text, flags=re.IGNORECASE):
            print(f"[API] Trigger word found, processing via Ollama ({OLLAMA_MODEL} @ {OLLAMA_EDIT_API_URL})...")

            # Strip the trigger in code and separate the spoken directions from the
            # content, so neither can leak into the output (see _split_on_trigger).
            content, instruction = _split_on_trigger(text, trigger_list)

            # Safety net: if the spoken instruction is really a roast command that
            # the roast patterns missed (e.g. Whisper heard "Prompt AI roast" as
            # "Prompte I roast"), route it to the roaster instead of the copy-editor.
            if instruction and ROAST_COMMAND_RE.match(instruction):
                print("[API] Edit instruction is a roast command; rerouting to roaster.")
                return _do_roast(content or text)

            if instruction and content:
                user_prompt = f"Editing instruction: {instruction}\n\nText to edit:\n{content}"
            elif instruction:
                # 'prompt AI <instruction>' with nothing before it: act on the instruction.
                user_prompt = instruction
            elif content:
                # Bare 'prompt AI' with no directions: just clean up the content.
                user_prompt = content
            else:
                # The whole utterance was only the trigger; nothing to do.
                return ""

            _fastlog(
                f"EDIT trigger fired -> POST {OLLAMA_EDIT_API_URL} model={OLLAMA_MODEL} | "
                f"content={content!r} instruction={instruction!r}"
            )
            try:
                response = requests.post(
                    OLLAMA_EDIT_API_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": user_prompt,
                        "system": (
                            "You are a personal copy editor for voice dictation. You turn a "
                            "raw speech-to-text transcript into clean, readable text that says "
                            "exactly what the speaker said.\n\n"
                            "Always: remove disfluencies (um, uh, er) and meaningless filler "
                            "(like, you know, I mean); add natural punctuation and "
                            "capitalization; fix obvious speech-to-text errors ONLY when "
                            "context makes the intended word unmistakable (e.g. \"jit hub\" -> "
                            "\"GitHub\"), and when in doubt keep the original word. Preserve the "
                            "speaker's own wording, slang, and casual voice exactly (\"gonna\" "
                            "stays \"gonna\"). Keep every idea the speaker expressed.\n\n"
                            "When writing math or symbols, prefer readable Unicode (e.g. "
                            "\"A² + B² = C²\") so it displays in any text field; only use "
                            "LaTeX notation (\\(...\\), ^, _) if the speaker asks for LaTeX.\n\n"
                            "The message may be plain dictated text to clean up, or it may be "
                            "a labeled \"Editing instruction:\" line followed by a \"Text to "
                            "edit:\" block. When an editing instruction is present, apply it to "
                            "the text and output only the result. When there is no instruction, "
                            "just clean up the text as described above.\n\n"
                            "Output the finished text only, beginning directly with the first "
                            "word. Plain text: no quotes, no markdown, no code fences, no bullet "
                            "points, and no preamble like \"Here is\". Do not summarize, "
                            "shorten, or add anything the speaker did not say."
                        ),
                        "stream": False,
                        # Copy-editing is a low-creativity task; Ollama defaults to
                        # 0.8 which invites the model to drift, invent, and add
                        # preambles. 0.2 keeps it close to the dictated text.
                        "options": {"temperature": 0.2},
                    },
                    timeout=60,
                )
                response.raise_for_status()
                data = response.json()
                # On any fallback, prefer the trigger-stripped content over the raw
                # transcript so a failure never pastes back "prompt AI ...".
                edited = data.get("response", content or text).strip()
                _fastlog(f"EDIT ok <- {len(edited)} chars")
                return edited
            except Exception as e:
                _fastlog(f"EDIT FAIL @ {OLLAMA_EDIT_API_URL}: {e!r}\n{traceback.format_exc()}")
                print(f"[API] Ollama Fast-Path error: {e}")
                return content or text

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

        # Transcribe.
        # vad_filter strips silent regions (Silero VAD) before the decoder sees
        # them, so a long thinking pause can't decode into the language prior's
        # favorite end-of-clip filler ("Thank you."). condition_on_previous_text
        # is off so a hallucination in one window can't seed the next.
        segments_gen, info = model.transcribe(
            temp_path,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        # Materialize once so we can inspect per-segment confidence scores.
        segments = list(segments_gen)

        # Confidence gate: split into trusted vs model-distrusted segments.
        kept, dropped = [], []
        for s in segments:
            (kept if _segment_is_reliable(s) else dropped).append(s)

        raw_text = "".join(s.text for s in segments).strip()
        text = "".join(s.text for s in kept).strip()

        # Fast-Path Processing Layer
        text = process_transcribed_text(text)

        # Corpus log: raw Whisper output, final text, per-clip confidence, and
        # any dropped segments.
        _log_transcript(raw_text, text, info, kept, dropped)

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

        # Transcribe (same VAD / no-carryover settings as /transcribe; see there).
        segments_gen, info = model.transcribe(
            temp_path,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )

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
