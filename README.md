# Transcript Studio

A local desktop app for transcribing recordings and reviewing the result. It runs Whisper on your
own GPU (or CPU), separates speakers, flags low-confidence sentences and non-speech sounds, lets
you listen and correct as you read, cleans up bad audio, and exports colour-coded markdown, PDF,
Word or HTML. Nothing leaves your computer and no account or token is needed.

<!-- screenshot: main window -->

- **Transcribe:** whisper.cpp (Vulkan GPU: AMD, Intel or NVIDIA), faster-whisper (NVIDIA CUDA) or
  CPU.
- **Review:** the transcript follows playback; click-free editing, speaker reassignment,
  participant names, sound tags.
- **Clean up:** hum, hiss, rumble, clipping and low levels, auditioned A/B on a selected range.
- **Export:** the structure is specified in [`spec.md`](spec.md) §8.

**Help → User Manual (F1)** has the full guide (`transcript_studio/docs/user_manual.md`);
**Help → About** shows the version, detected GPU, available engines and credits.

## Try it

There are sample files in [`samples/`](samples/README.md): a two-speaker NASA clip, and a copy of
it with hum, hiss, a low level and a clipped passage added on purpose.

```
.venv/bin/python -m transcript_studio                 # then File → Open Audio…
.venv/bin/python -m transcript_studio samples/multispeaker/nasa-podcast-ep434-2speakers.tsproj.json   # a transcribed project
.venv/bin/python -m transcript_studio.cli samples/multispeaker/nasa-podcast-ep434-2speakers.mp3 -m medium.en -o out.md   # headless
.venv/bin/python -m pytest tests
```

To learn the cleanup tools, follow [`docs/tutorial-cleanup.md`](docs/tutorial-cleanup.md).

## Setup from scratch (Linux)

```
sudo apt install ffmpeg cmake glslc vulkan-tools
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
tools/build_whisper_cpp.sh
```

`tools/build_whisper_cpp.sh` clones a pinned whisper.cpp into `vendor/`, builds it with the
Vulkan backend and downloads the `medium.en` model.

Dock / app-menu launcher: `tools/install_desktop.sh` installs
`~/.local/share/applications/transcript-studio.desktop` and the icon, and pins
it to the Ubuntu Dock. `--uninstall` removes all three.

## GPU support

Transcription goes through a backend layer (`transcript_studio/backends.py`).
The first available engine in this order is picked automatically, and the
"Engine" dropdown can override it:

| Engine | When | Notes |
|---|---|---|
| faster-whisper, CUDA float16 | NVIDIA GPU present | Uses `vad_filter=True`. |
| whisper.cpp, Vulkan | Vulkan GPU present | Built by `tools/build_whisper_cpp.sh`. Runs without VAD, because whisper.cpp's VAD drops coughs and breaks word timestamps. |
| faster-whisper, CPU int8 | fallback | Slow with medium/large models. |

Measured on one older AMD GPU (Polaris, 4 GB), a 14.2 min recording, medium.en, whisper.cpp/Vulkan:
transcription ~225–285 s (≈3–4× faster than real time), GPU ~90% busy, CPU
mostly idle. `tools/gpu_watch.sh <cmd>` samples GPU load during a run (AMD
sysfs or `nvidia-smi`).

**With an NVIDIA card:** install the driver, then
`.venv/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` (if the system
has no CUDA 12 / cuDNN 9) and point `LD_LIBRARY_PATH` at their `lib`
directories. The faster-whisper CUDA engine becomes available and is
picked automatically. faster-whisper models download from Hugging Face
(ungated, no token) on first use.

## License

GPL-3.0, see [`LICENSE`](LICENSE). Third-party components and the NASA sample audio are credited
in [`NOTICE`](NOTICE); the NASA audio keeps its own terms.

## Waveform

The playback panel shows the recording's waveform:
- **Zoom:** mouse wheel, or the −/+/Fit buttons.
- **Scroll:** Shift+wheel or the scrollbar. While playing, the view follows
  the playhead (the "Follow playback" checkbox).
- **Colours:** after transcription it's coloured exactly like the text
  (speaker colour, red for low-confidence, gray for non-speech, neutral for
  untranscribed audio). Speaker or 🔴 changes recolour it immediately.
- **Selection:** drag to select a range. Play then starts at the selection
  and stops at its end, ready to replay, or repeats with **Loop**. That makes
  it easy to listen to one bad patch over and over. Click, Esc or "Clear
  selection" removes it.
- **Words lane** (the **Words** toggle, remembered): zoomed in to 40 s or
  less, each transcript word is drawn under its own audio, in its speaker's
  colour (sound annotations in italics). It scrolls with playback, and the
  word being spoken is highlighted.
- **Tag sound…** (needs a selection) labels a non-speech sound: type a word,
  or pick one (sneeze, horn, bell, door, dog barking…).
  - The tag is saved in the project and shown as a purple box in the words
    lane. Right-click the box to remove the tag, and its text.
  - With a transcript, "(sneeze)" is inserted straight away between the
    adjacent words. Otherwise it's inserted when you transcribe, and again on
    every re-transcription.
  - Annotations in parentheses/brackets inside spoken sentences show in
    italics in the editor and export as `_(sneeze)_`.
- **Rendered preview:** "Play rendered" shows the rendered file's waveform.
- **Selection → text:** a waveform selection scrolls the transcript to that
  point and shows the matching words **inverted** (white on the speaker's
  colour). It's word-level where word timestamps exist, and display-only, so
  the text isn't changed.
- **Text → waveform (right-click a sentence):**
  - **Center waveform here** moves the waveform *view* to the clicked word.
    Playback position is unchanged, per the spec's rule that text never moves
    the audio.
  - **Select this sentence in the waveform** sets the selection to that
    sentence, centred and zoomed so it fills 2/3 of the waveform width.
    Play (or Loop) then plays just it.

## Participant names

**Transcript → Name Participants…** (Ctrl+Shift+N) gives each participant a
name. To help you tell who's who, each row shows their colour, talk time,
when they first speak, and their opening words. Names replace
"Participant N" in the speaker dropdowns and in the export: paragraph
headers, legend and per-participant stats. They're saved in the project
file. Leave a name blank to keep the number. Names belong to the participant
number, so reassigning a paragraph's speaker shows that speaker's name.

## Export formats

**File → Export… (Ctrl+E)** opens a dialog:
- **Format:** Markdown, PDF (Letter, page-numbered, rendered by Qt) or Word
  .docx (via python-docx).
- **Details:** include or leave out the file & transcription details tables.
- **Save to:** the location, typed or chosen with Browse….

The last folder, format and details choice are remembered. PDF and Word
carry the same content and colours as the markdown, with the colour emoji
drawn as coloured ● dots, because emoji don't render reliably in those
formats. The CLI picks the format from the `-o` extension (`.md` / `.pdf` /
`.docx`) and takes `--no-details`. PDF export also works with no display.

## Export header

With "File & transcription details" ticked (the default), the markdown gets
two tables after the intro:
- **File details:** format, sample rate and channels, bitrate, duration,
  size, recording date and device (from metadata), loudness, and the boosted
  copy with how it was boosted.
- **Transcription details:** engine and model, when it ran, how long it took
  and the speed, speaker model and speakers found, counts, low-confidence and
  mean word confidence, non-speech events, talk time and word count per
  participant, and export time.

Untick it for the exact legacy format. Run stats are saved in the project
file; content stats are computed at export, so they reflect your edits.

## Progress, stats and logs

- **Progress:** transcription runs as 6 steps (preparing audio → loading the
  Whisper model → transcribing → loading the speaker model → separating
  speakers → building the transcript). Each step has a progress bar, which
  animates when the length is unknown. First-use model downloads show
  MB / total MB and speed.
- **Live stats:** elapsed time, step ETA, audio processed and speed (× real
  time), engine/model, GPU busy % and VRAM (AMD sysfs or `nvidia-smi`), CPU
  and RAM, and the latest transcribed line.
- **Log file:** switch it with "Write log file" (Transcription panel or the
  Log menu; remembered between sessions, on by default). One file per session
  goes in `~/.local/state/transcript-studio/logs/`, keeping the last 30. It
  records versions, available engines, every step with timings, the ffmpeg
  and whisper.cpp commands with their output (GPU device, model), downloads,
  and full tracebacks on errors. Error dialogs have an "Open log" button. The
  CLI takes `--log`.

## Behaviour notes / tuning knobs

- **Structure** (matches `samples/multispeaker/nasa-podcast-ep434-2speakers.format-reference.md`): each
  Whisper segment is split into sentences at `.?!`. A new paragraph starts on
  a speaker change, a pause ≥ 2 s, or a sentence end after 30 s
  (`PARAGRAPH_GAP`, `PARAGRAPH_MAX` in `pipeline.py`).
- **Non-speech**: `(…)`, `[…]` and whisper.cpp's `*…*` annotations become gray
  paragraphs (`*coughing*` is rewritten as `(coughing)`), and `[BLANK_AUDIO]`
  is dropped.
- **Low confidence**: mean word probability < threshold (default 0.5).
  whisper.cpp's probabilities run high (median sentence ≈ 0.93), so 0.5 flags
  very little; raise it in the UI if you want more flags.
- **Speakers**: voice embeddings + agglomerative clustering, with the speaker
  count picked automatically (silhouette score, up to 4) or set by hand.
  Two voice models, neither needing an account or token:
  - **ECAPA** (`speechbrain/spkrec-ecapa-voxceleb`), the default. It's an
    ungated one-time ~80 MB download to `~/.cache`, and takes ~4–5 min of CPU
    per hour of audio.
  - **resemblyzer**, the fast fallback, used automatically if ECAPA can't
    load. Weights ship with the pip package; ~1 min per hour of audio.

  Speakers are numbered by first appearance. Settings are tuned per model
  (`PARAMS_BY_ENCODER` in `diarize.py`).
- **Diarization testing**: `samples/multispeaker/` has public-domain NASA
  podcast clips (1, 2 and 3 speakers) with speaker-labelled ground truth.
  `tools/eval_diarization.py [--encoder X] [--sweep]` scores settings in
  seconds (transcripts and embeddings are cached in `.cache/`), and
  `tools/score_diarization.py <project> <reference>` scores any project.
  Current results, as the share of speech given to the right person:

  | Clip | ECAPA | resemblyzer |
  |---|---|---|
  | 1 speaker | 100%, K 1/1 | 100%, K 1/1 |
  | 2 speakers (two women) | 96.9%, K 2/2 | 96.5%, K 2/2 |
  | 3 speakers (three men) | 99.0%, K 3/3 | 97.6%, K 3/3 |

  ECAPA got the speaker count right with every one of the 288 settings in
  the sweep; resemblyzer only within a narrow band. The remaining errors are
  mostly interjections that Whisper merged into the other person's sentence.
- **Playback copy**: every opened file is converted to `<name>.ts.flac` (mono,
  16-bit, original sample rate) next to it, or under
  `~/.cache/transcript-studio` if that folder is read-only. Playback, boosting
  and transcription all use this copy, so they share one sample-accurate
  timeline whatever the input format (VBR MP3 seeks inaccurately in players).
  Derived files are stamped with their source's mtime and reused while the
  stamps match, so a future-dated source doesn't trigger reconversion.
- **Speech cleanup** (replaces the old boost) works on the *original* file
  and writes mono FLAC `<name>.boosted.flac`, the same rate and length as the
  playback copy. The panel has:
  - preset and channel (mix / left / right / left−right)
  - low cut, high cut, hum notch (50/60 Hz + 5 harmonics), presence (+dB at 3 kHz)
  - de-click, de-clip
  - noise reduction: FFT `afftdn`, wavelet `afwtdn`, RNNoise `arnndn`, or
    NL-means `anlmdn` (slow), each light/medium/strong
  - leveller: `speechnorm` / `dynaudnorm`
  - normalise (−16 LUFS), gain, and a limiter at the end

  Presets: Normalize only, Gentle (for transcription), Rumble & hiss,
  Telephone, Quiet far speaker, Hum removal, Heavy noise (RNNoise). Any manual
  change shows "Custom", and settings are remembered. RNNoise's ~300 KB model
  (`models/rnnoise/cb.rnnn`, public, no account) downloads on first use.
- **Zero timeline shift:** measured delays (FFT 25 ms, NL-means 8 ms,
  RNNoise 10 ms, limiter look-ahead) are trimmed back off. RNNoise's padding
  to whole 480-sample frames is cut back to the exact source length (after
  resampling back from loudnorm's internal 192 kHz). `tests/test_audio.py`
  checks every filter, every preset and an everything-on chain for equal
  length and < 2 ms shift. The only residual is EQ phase, under 1 ms.
- **Selection audition (A/B):** select a range on the waveform, then press
  **Audition selection**. Just that range (plus 1 s of lead-in) is processed
  in well under a second and loops. Toggle the button or press **Ctrl+B** to
  flip between processed and original at the same spot. Changing any setting
  re-renders after a short pause, so you can tweak while listening. All
  positions stay in recording time (transcript highlight, waveform, clock),
  and seeking outside the range ends the audition.
- **Cleanup plan:** the Speech cleanup tab builds an edit list that's saved in
  the project.
  - **Apply to** chooses what **Save to plan** saves:
    - **Selected range** adds a numbered *block*: start/end plus settings.
      Ranges may not overlap; overlaps are refused with a message.
    - **Whole file** sets the base settings used everywhere else.
  - **Pick an entry to edit:** click it in the plan list, or click its amber
    band above the waveform. Its settings and range load back in. Adjust the
    range by dragging the selection edges or typing **Start / End**, then
    **Update block N**. **New block** and **Delete** do what they say.
  - **Render cleaned audio** writes `<name>.boosted.flac`: the whole-file base
    (or the untouched original), with each block's own processing spliced in
    at its time slot, crossfaded 20 ms at each edge so there are no clicks.
    It has the same length and timeline as the original. The plan note says
    whether the rendered file is up to date. Rendering always starts from the
    original, so you can keep editing the plan and re-render.
  - Then **Play rendered** / **Transcribe rendered**, or **Save as new
    recording…** to save the rendered file under a normal name and open it
    as a fresh recording for another cleanup pass.
  - A project exists as soon as a file is opened, so a plan can be saved
    before transcribing; transcribing keeps the plan.
- Projects save as `<name>.tsproj.json`. If you move a project together with
  its audio file, the audio is still found.
