"""
Compare models on the BASE 'prompt ai' copy-editor task (not mean-mode).

Question: can the mean-mode model (richardyoung 14b) also handle everyday
dictation corrections, so you run ONE model for everything, or does the small
fast qwen2.5:7b still earn its keep for quick fixes?

Runs the SAME base system prompt the API uses (api/whisper_api.py -> normal
trigger branch) over realistic self-correction scenarios, and flags the failure
modes that matter for a paste-directly-into-the-box tool: preamble/commentary,
echoing the instruction, markdown, over-rewriting, and latency.

Usage:  .venv/Scripts/python scripts/compare_edit_models.py
"""

import re
import sys

sys.path.insert(0, "scripts")
from compare_roast_models import generate, installed_models  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Mirror of the base editor system prompt in api/whisper_api.py.
BASE_SYSTEM = (
    "You are a personal copy editor. Clean up the following dictated text for "
    "clarity and flow. Fix transcription errors and remove filler words. Do not "
    "use bullet points, string text only. If the text contains 'prompt ai' "
    "followed by instructions, prioritize those instructions. Return ONLY the "
    "corrected text, without any additional commentary or explanation unless "
    "it's asked for by the user."
)

CANDIDATES = [
    "qwen2.5:7b-instruct-q4_K_M",
    "richardyoung/qwen2.5-14b-instruct-abliterated",
]

# Each scenario: dictated input + what a good result must contain / must not.
SCENARIOS = [
    {
        "name": "car mixup (self-correction)",
        "text": ("and that's why the suzuki I mean Subaru is the best option. "
                 "Prompt AI, hey can you fix the ending there so i don't mix up the cars?"),
        "must_have": ["Subaru"],
        "must_not": ["suzuki", "I mean", "mix up"],
    },
    {
        "name": "date mixup",
        "text": ("so the movie is on Wednesday, I mean Tuesday, actually it's "
                 "Thursday at noon. prompt ai can you make it clear it's Thursday at noon?"),
        "must_have": ["Thursday", "noon"],
        "must_not": ["Wednesday", "Tuesday", "I mean"],
    },
    {
        "name": "filler cleanup",
        "text": ("um so like i was thinking that we should uh maybe you know push "
                 "the deadline to next friday or something. prompt ai clean this up"),
        "must_have": ["Friday"],
        "must_not": [" um ", " uh ", "you know"],
    },
    {
        "name": "tone shift to professional",
        "text": ("hey the thing you sent over is kinda broken and im pretty annoyed, "
                 "can you fix it asap. prompt ai make this sound professional and polite"),
        "must_have": [],
        "must_not": ["annoyed", "kinda"],
    },
    {
        "name": "de-list into a sentence (no bullets)",
        "text": ("ok for the trip we need sunscreen, and also i gotta remember the "
                 "chargers, oh and snacks, and the tickets obviously. prompt ai turn "
                 "this into one clean sentence, no lists"),
        "must_have": ["sunscreen", "snacks"],
        "must_not": ["\n- ", "\n* ", "\n1."],
    },
    {
        "name": "light grammar fix (minimal edit)",
        "text": ("i'll circle back to you on monday regarding the budget prompt ai "
                 "just fix any grammar don't change my wording"),
        "must_have": ["Monday", "budget"],
        "must_not": [],
    },
]

PREAMBLE = [
    "sure", "certainly", "here is", "here's", "here are", "of course", "okay,",
    "ok,", "i've", "i have", "corrected text", "the corrected", "as requested",
    "absolutely", "got it",
]
TRAILING = ["let me know", "hope this", "feel free", "is this", "would you like"]


def check(text: str, sc: dict) -> tuple:
    low = text.lower()
    flags = []
    if any(low.lstrip('"\'').startswith(p) for p in PREAMBLE):
        flags.append("PREAMBLE")
    if any(t in low for t in TRAILING):
        flags.append("trailing-chatter")
    if "prompt ai" in low or "promptai" in low:
        flags.append("echoed-instruction")
    if re.search(r"(^|\n)\s*[-*#]|\*\*", text):
        flags.append("markdown")
    missing = [w for w in sc["must_have"] if w.lower() not in low]
    leaked = [w for w in sc["must_not"] if w.lower() in low]
    if missing:
        flags.append(f"missing={missing}")
    if leaked:
        flags.append(f"leaked={leaked}")
    ok = not flags
    return ok, flags


def main():
    have = installed_models()
    models = [next((n for n in have if n.startswith(c)), None) or c for c in CANDIDATES]
    models = [m for m in models if any(h.startswith(m.split(':')[0]) for h in have) or m in have]
    print("Testing base 'prompt ai' editor on:", ", ".join(models), "\n")

    tally = {m: {"pass": 0, "secs": 0.0} for m in models}
    for sc in SCENARIOS:
        print("=" * 78)
        print(f"SCENARIO: {sc['name']}")
        print(f"  input: {sc['text']}")
        print("=" * 78)
        for m in models:
            try:
                res = generate(m, BASE_SYSTEM, sc["text"])
            except Exception as e:
                print(f"\n[{m}] ERROR: {e}")
                continue
            ok, flags = check(res["text"], sc)
            tally[m]["secs"] += res["secs"]
            if ok:
                tally[m]["pass"] += 1
            mark = "OK  " if ok else "FAIL"
            fstr = "" if ok else "  !! " + ", ".join(flags)
            print(f"\n--- [{mark}] {m}  ({res['secs']:.1f}s){fstr}")
            print(res["text"])
        print()

    print("#" * 78)
    print("SUMMARY (clean, faithful, paste-ready fixes)")
    print("#" * 78)
    n = len(SCENARIOS)
    for m in sorted(models, key=lambda x: tally[x]["pass"], reverse=True):
        t = tally[m]
        avg = t["secs"] / n if n else 0
        print(f"  {t['pass']}/{n} clean   {avg:4.1f}s avg/fix   {m}")


if __name__ == "__main__":
    main()
