# Transcript Studio — Desktop Transcription & Review App — Build Spec

## 1. Purpose

A desktop app (runs on an Ubuntu workstation with an NVIDIA GPU) that
replaces the previous manual/cloud transcription workflow. The user loads an
audio file, optionally boosts/filters it, transcribes it locally with
GPU-accelerated Whisper, reviews and edits the color-coded transcript while
listening, and exports a markdown file in the same format as the previous
pipeline produced.

This document is self-contained — written so it can be dropped into a fresh
project/session to build from, without needing the conversation that produced
it.

## 2. Goals

- Load an audio file (m4a/mp3/wav/etc.) and play it back.
- Optionally boost volume / apply filtering to the audio before transcribing
  (and preview the boosted audio before committing).
- Transcribe using `faster-whisper` on GPU, with the **Whisper model size
  chosen per-transcription** (not hardcoded) from a dropdown.
- Automatically separate speakers and flag non-speech sounds (coughing, etc.)
  and low-confidence words, same as the previous pipeline — **without any
  Hugging Face token or gated model**. (The previous pipeline's speaker
  separation and noise detection worked well with no auth/gating; see §6.)
- Show the transcript color-coded by speaker/confidence/non-speech, editable
  in place.
- Keep audio position and transcript view in sync as the user listens: the
  transcript auto-highlights/scrolls to track playback position.
- Let the user correct transcript text and reassign speaker labels.
- Export a markdown file matching the legacy format exactly (§8).

## 3. Non-goals / explicit constraints

- **No click-on-text-to-seek.** Clicking in the transcript must never move
  the audio playback position. Audio navigation is *only* via standard
  transport controls: play/pause, rewind-by-N-seconds, forward-by-N-seconds,
  and jump-to-a-specific-time. This is deliberate, to keep the sync logic
  one-directional and simple: **audio position drives the transcript
  highlight, never the reverse.**
- No cloud APIs, no services requiring an account/token/API key for the core
  transcription or diarization path.
- No requirement to reproduce the old web player (`player.html`) or the
  gated-webapp delivery mechanism — this is a local desktop app instead.

## 4. Tech stack

| Concern | Choice | Why |
|---|---|---|
| GUI | **PySide6** (Qt for Python) | LGPL, avoids PyQt6's GPL/commercial licensing; this is the project's established default for desktop apps. |
| Audio playback | `QtMultimedia` (`QMediaPlayer` + `QAudioOutput`) | Built into PySide6, no extra native deps. |
| Transcription | `faster-whisper` (CTranslate2 backend) | What the previous pipeline used; GPU-accelerated, word-level timestamps + per-word confidence. |
| Speaker diarization | `resemblyzer` (speaker-embedding) + clustering (`scikit-learn` AgglomerativeClustering, cosine distance) | Ships its own pretrained weights inside the pip package — no HF download, no token, no gating. Matches "no token/gated model" requirement. |
| Voice activity detection | `faster-whisper`'s built-in `vad_filter` (Silero VAD under the hood) | Already bundled, no separate install. |
| Audio decode/boost | `ffmpeg` (system binary, invoked via `subprocess`) | Decode any container, apply gain/loudnorm/noise-filter chains. |
| Language | Python 3.10+ | Matches available toolchain. |

Install list for the target workstation (for the builder session to run, one command at a
time, verifying GPU is picked up at each step):

```
sudo apt install ffmpeg
python3 -m venv .venv
.venv/bin/pip install PySide6 faster-whisper resemblyzer scikit-learn soundfile
```

`faster-whisper` needs cuDNN/cuBLAS for GPU inference. Two paths, pick
whichever the builder session verifies works on the target machine:
1. System CUDA toolkit + cuDNN already installed via NVIDIA's apt repo (check
   `nvidia-smi` and existing CUDA install first), or
2. The pip-installable cuDNN/cuBLAS wheels (`nvidia-cudnn-cu12`,
   `nvidia-cublas-cu12`) with `LD_LIBRARY_PATH` pointed at their install
   location — avoids a system-wide CUDA toolkit install.

Verify GPU is actually used by checking `faster-whisper`'s `WhisperModel(...,
device="cuda")` loads without falling back to CPU, and watching `nvidia-smi`
during a transcription run.

## 5. Processing pipeline

1. **Load** — decode input audio via ffmpeg to 16kHz mono PCM for the model;
   keep the original (and any boosted version) for playback.
2. **Boost/filter (optional, user-triggered)** — apply an ffmpeg filter chain
   (volume gain in dB, and/or `loudnorm`, and/or basic noise reduction via
   `afftdn`) to produce a boosted audio file. User can preview it (play
   button) before accepting it as the working audio for transcription.
3. **Transcribe** — `faster-whisper` with:
   - `model_size` = whatever the user picked in the model dropdown
   - `device="cuda"`, GPU compute type (e.g. `float16`)
   - `word_timestamps=True` (needed for confidence + sync highlighting)
   - `vad_filter=True` (skip silence)
4. **Non-speech detection** — scan each segment's text for
   parenthetical/bracketed non-verbal annotations that Whisper itself
   produces (e.g. `(coughing)`, `(laughs)`, `[inaudible]`) via regex. Flag
   those segments as non-speech events rather than attributing them to a
   speaker. (This is how the previous pipeline detected "coughing" — it's
   Whisper's own behavior, not a separate classifier.)
5. **Low-confidence flagging** — per word, use `word.probability` from
   faster-whisper's word timestamps. Flag a sentence low-confidence if its
   average (or minimum) word probability falls below a threshold (default
   0.5, make configurable).
6. **Speaker diarization** — for each non-silent, non-flagged-as-noise
   segment: extract a speaker embedding with `resemblyzer`, then cluster all
   embeddings (agglomerative, cosine distance) into *K* speakers. Let the
   user set an expected speaker count, or auto-pick *K* via silhouette score
   up to a max (default max 4). Assign a speaker id to each segment.
7. **Sentence split** — split each segment's text into sentences
   (punctuation-based, matching the legacy numbering scheme: paragraph =
   segment, sentence = sub-item).
8. **Build the data model** (§7) and hand it to the UI for display/editing.

## 6. Why this diarization approach (context for the builder)

The previous pipeline achieved good speaker separation and automatic
"coughing" detection **without any token or gated model** — confirmed by the
user. That rules out pyannote's gated pipelines (`pyannote/speaker-diarization-3.1`
etc., which require accepting terms on huggingface.co and an HF token). The
non-speech detection is just Whisper's own transcription behavior (it was
trained on some data containing bracketed non-verbal annotations), not a
separate sound classifier. `resemblyzer` is the standard no-auth,
ships-its-own-weights option for the embedding+clustering half of this — if
the builder session finds it insufficient in testing, the fallback is an
ungated (but HF-hosted) embedding model like `speechbrain/spkrec-ecapa-voxceleb`,
which still needs no gating/token, just a one-time model download.

## 7. Data model

Per transcription session, build a tree:

```
Session
├── audio_path (original)
├── boosted_audio_path (optional, if boost was applied)
├── model_size (string, e.g. "large-v3-turbo")
└── paragraphs: [Paragraph]

Paragraph
├── index (1-based, matches legacy "### N.")
├── speaker_id (int, or null if non-speech)
├── is_nonspeech (bool)
├── start_time (seconds)
└── sentences: [Sentence]

Sentence
├── index (1-based within paragraph, matches legacy "N.M")
├── text (string, user-editable)
├── low_confidence (bool, user-overridable)
├── words: [Word]   # kept for the confidence calc and for future re-sync if needed

Word
├── text
├── start_time
├── end_time
└── probability
```

**Persistence:** support saving/reloading this whole tree as a JSON "project
file" alongside the audio, so a review/edit session can be closed and
resumed without re-transcribing. This is required for the editing workflow
to be usable (edits must not be lost between sessions).

## 8. Markdown export format (must match exactly)

Reproduce this structure (colors and emoji are fixed; extend the speaker
palette if there are more than 3 speakers found):

```markdown
# <Title> Transcript

Automated transcript (faster-whisper, model: <model_size>) with best-effort
speaker separation. Speaker labels and the unintelligible/low-confidence flag
are machine-generated — verify against the audio before relying on them.

**Legend:**

- 🔵 <span style="color:#1d4ed8">Participant 1</span>
- 🟢 <span style="color:#059669">Participant 2</span>   <!-- only if present -->
- 🟠 <span style="color:#d97706">Participant 3</span>   <!-- only if present -->
- 🔴 <span style="color:#dc2626">Low-confidence / possibly unintelligible</span>
- ⚫ <span style="color:#6b7280">Non-speech sound (e.g. coughing) — automatically detected</span>

---

### <paragraph#>. <emoji> Participant <N> — _<HH:MM:SS>_

**<paragraph#>.<sentence#>** <optional 🔴> <span style="color:<color>"><sentence text></span>  
**<paragraph#>.<sentence#+1>** ...
```

Rules:
- Paragraph header emoji is one of 🔵🟢🟠 for a speaker, or ⚫ for a
  non-speech paragraph (no "Participant N —" text in that case, just the
  timestamp: `### N. ⚫ _HH:MM:SS_`).
- Non-speech sentences render as `**N.M** ⚫ <span style="color:#6b7280">_(coughing)_</span>`
  (italic, gray, using Whisper's own bracketed text).
- Low-confidence sentences get a `🔴` prefix and the span color switches to
  `#dc2626` red (overriding the speaker color) for that sentence only.
- Every sentence line except the last one in a paragraph ends with two
  trailing spaces (markdown hard line break).
- Timestamps are `HH:MM:SS` from the paragraph's start time.

## 9. UI layout

**Transport / playback panel** (top, always visible):
- Open file button
- Play / pause
- Rewind N seconds, forward N seconds (N configurable, default 10s; a couple
  of preset buttons e.g. 5s/10s/30s plus a free-entry seconds field is fine)
- Jump-to-timestamp field
- Current position / total duration display
- A position progress indicator is fine as a *standard* transport element
  (e.g. a draggable seek slider) — this is not "clicking on text," it's
  normal media-player behavior, so it's in scope even though text-clicking
  is not.
- Boost/filter controls: gain slider (dB), normalize toggle, noise-reduction
  toggle, "preview boosted audio" toggle (switches playback source between
  original and boosted without affecting the transcript).

**Transcription panel:**
- Model-size dropdown (populated from the faster-whisper model list;
  selectable fresh for every run, not a fixed default)
- Expected speaker count (optional override; default auto)
- Low-confidence threshold (optional, default 0.5)
- "Transcribe" button + progress indicator

**Transcript panel (main area):**
- Scrollable, color-coded per §8's palette (speaker colors, red
  low-confidence, gray italic non-speech)
- Auto-highlights and auto-scrolls to the sentence whose time range contains
  the current playback position — this is a **one-way sync**: audio →
  transcript highlight, never transcript click → audio.
- Text is directly editable in place (editing moves the text cursor for
  typing, which is unrelated to audio seeking — that's fine, it's text
  editing, not navigation).
- Per-paragraph speaker reassignment control (e.g. a small dropdown next to
  the paragraph header, like the old web player had).
- Manual low-confidence toggle per sentence (for correcting false
  positives/negatives).

**Export:**
- "Export Markdown" — writes the §8-format file.
- "Save Project" / "Open Project" — JSON project file per §7, for
  pause-and-resume editing.

## 10. Acceptance criteria

- Loading a large (1hr+) audio file and transcribing it on the workstation's GPU
  completes in a small fraction of real-time (verify against `nvidia-smi`
  showing GPU utilization during the run, not CPU fallback).
- Re-running the same file with a different model-size selection produces a
  different transcript without restarting the app.
- A multi-speaker test recording gets distinct, mostly-correct speaker
  labels with no token/account/gated-model setup required.
- A recording with a cough/laugh gets it flagged as a non-speech event, not
  attributed to a speaker.
- Clicking anywhere in the transcript text never changes playback position.
- Playing audio moves the transcript highlight/scroll position in step,
  continuously, without needing to click anything.
- Editing transcript text and reassigning a paragraph's speaker, then
  exporting, produces a markdown file matching §8's format with the edits
  applied.
- Closing and reopening a saved project restores the transcript, edits, and
  speaker assignments without re-transcribing.

## 11. Open decisions left to the builder session

- Exact ffmpeg filter chain for "boosting" (plain volume gain vs.
  `loudnorm` vs. a combination) — pick based on testing against real
  recordings on the target machine.
- Auto speaker-count selection method (silhouette score vs. a fixed default
  max) — tune against real multi-speaker test audio.
- Whether to also keep an HF-hosted-but-ungated embedding model
  (`speechbrain/spkrec-ecapa-voxceleb`) as a fallback if `resemblyzer`
  underperforms in testing.
