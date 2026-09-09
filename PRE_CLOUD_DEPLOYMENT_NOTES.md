# Pre-Cloud Deployment Notes

Things in this repo that are correct for **this** deployment — Dom's personal,
single-user, persistently-loaded Faster-Whisper box — and are **wrong** for a
multi-tenant cloud version. Nothing here is a bug to fix locally. Each item is a
deliberate single-user choice that stops being valid the moment a second person
uses the service.

Spinning the API itself up in the cloud is not the hard part: it is a persistently
loaded model behind a FastAPI process. The hard part is that a good deal of what
makes it *pleasant* here is personal state baked into the code path.

---

## 1. Per-user text rewriting must not ship (`name_corrections`)

`api/whisper_api.py:182` — `load_config()` ships a hardcoded default:

```python
"name_corrections": {"Leslie": "Lesley", "Emma": "Ame"},
```

overridable via `api/processing_config.json`. These are *Dom's* contacts. They are
meaningless-to-harmful for any other user: a cloud tenant who knows a Leslie gets
her name silently misspelled on every transcript.

**Also fix the design, not just the data.** The map is applied unconditionally, so
it corrupts dictation *about* the spelling itself. Real example, 2026-08-06 18:23:50:

```
RAW  : "...is Leslie an ey, because that's my girlfriend, not Leslie with an ie at the end."
FINAL: "...is Lesley an ey, because that's my girlfriend, not Lesley with an ie at the end."
```

Both spellings were rewritten to the same string and the sentence became
meaningless. Same again at 18:24:11. This is the only mechanism observed that
night that actually destroyed real dictated content — background music, FM radio,
and mic distance did not.

For cloud:
- Per-account, never global, never a code default.
- Suppress the substitution when the utterance is *about* orthography (contains
  "spelled", "with an ie", "an ey", "not X with", etc.), or make it undoable.
- Contact-derived corrections belong in a user profile, not in `load_config()`.

Deliberately **not** fixed locally: this is the personal API and the map is right
for it. See also the standing rule that this pipeline never removes words the
model actually heard — a rewrite that eats real speech violates the same principle.

## 2. Trigger words: remove entirely, or make them per-client

Three separate trigger families are currently global constants:

| Family | Where | Effect |
|---|---|---|
| `trigger_patterns` ("prompt ai") | `whisper_api.py:183` | Rewrites the transcript via an LLM |
| `roast_trigger_patterns` | `whisper_api.py:194` | Routes to mean-mode / roast generation |
| `CORPUS_TAG_TRIGGERS` | `whisper_api.py:67`, `.env` | Files the clip permanently into `keep/` |

Problems in a shared deployment:

- **Every tenant shares one trigger vocabulary.** One user saying a common word
  changes another user's expectations of the product.
- **`CORPUS_TAG_TRIGGERS` now contains bare `loud`** (added 2026-08-06). That is a
  fine trade for a single user who wants noisy clips saved and accepts a few false
  positives. In the cloud it means an ordinary English word causes *permanent,
  never-pruned retention of a stranger's audio*. That is a privacy issue, not a
  tuning issue.
- **Roast/mean-mode is a personal-instance feature.** It should not exist in a
  business-facing deployment at all, or must be explicitly opt-in per account.

For cloud: either strip triggers entirely and drive these features from the client
UI (an explicit button beats a spoken magic word), or load the whole trigger set
per-client from account config with a safe empty default. Do not carry the local
defaults forward.

## 3. Denoise: leave it off, and consider dropping the dependency

`DENOISE_ENABLED=false`. The evidence for keeping it off is now reasonably strong:

- The 2026-08-03 dual-pass evaluation found no measurable win.
- 2026-08-06, 55 clips: deliberately adversarial conditions (moving car, YouTube
  Music through bluetooth speakers with BT latency, FM radio, speech at whisper
  through to shouting, phone held close / medium / arm's length) averaged
  **wlogp -0.169** — *better* than the same session's quiet-room dictation at
  **-0.244**. Zero transcription errors were attributable to background noise.

Modern phone mics plus on-device voice isolation appear to be doing the work.
DeepFilterNet is a bundled CLI + model; if it stays off, dropping it removes real
weight from a cloud image. Keep the code path behind the flag if you want the
option, but do not pay for it in the base image by default.

Caveat worth preserving: denoise cannot help the case that actually fails, which
is **mic distance and competing speech** (a restaurant), not background music.
That is a near-field/far-field problem, and no denoiser removes another person's
voice.

## 4. Private data written to local disk

Both of these hold real speech and must be per-tenant isolated, retention-bounded,
and disclosed before any multi-user deployment:

- `api/transcripts.log` — every raw + final transcript (gitignored).
- `api/audio_corpus/` — rolling source audio, with `keep/` **never pruned**.

`KEEP_AUDIO_MAX` / `KEEP_AUDIO_HARD_MAX` bound the rolling folders by count, but
`keep/` is unbounded by design. Combined with a bare-word trigger (see §2), that
is an unbounded audio retention path triggered by a common word.

## 5. Corpus confidence gating is tuned on one voice

`KEEP_AUDIO_PIN_BELOW=-0.25` was derived from 414 clips of a single speaker, and
the length-adjustment constants (`KEEP_AUDIO_PIN_REF_DUR=15.0`,
`KEEP_AUDIO_PIN_DUR_SLOPE=0.05`, added 2026-08-06) were fitted to 54 clips of that
same speaker on one night.

The *shape* of the correction should generalise — wlogp rises with utterance
length for everyone, corr(duration, wlogp) = +0.55 in that sample — but the
constants should be re-fitted per deployment, or per account, rather than assumed.
Do not treat these numbers as universal.
