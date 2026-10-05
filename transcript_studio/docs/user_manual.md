# Transcript Studio — User Manual

## Overview

Transcript Studio turns a recording into a colour-coded, speaker-labelled
transcript that you can check and correct while you listen. It then exports
the transcript as a markdown file. Everything runs on this computer: no cloud
service, account or token is needed.

A typical session:

1. **Open** a recording (File → Open Audio).
2. Optionally **clean up** hard-to-hear parts (Speech cleanup tab).
3. **Transcribe** it (Transcription tab).
4. **Review** while listening: fix words, speakers and flags, name the
   participants, and tag sounds.
5. **Save** the project, then **export** the markdown.

The window, top to bottom:

- **Playback:** play, skip, jump, the position slider and the time display.
- **Waveform:** the recording drawn as a zoomable picture, with selection,
  cleanup blocks and the words lane.
- **Speech cleanup / Transcription tabs:** the controls for each job, plus
  progress.
- **Transcript:** the editable, colour-coded text.

## Opening recordings and projects

- **File → Open Audio… (Ctrl+O)** opens m4a, mp3, wav, flac, ogg, opus,
  aac, wma, mp4 or webm. The app first makes a *playback copy*
  (`<name>.ts.flac`) next to the file. That takes about a second for a
  14-minute recording and happens only once. Playback, cleanup and
  transcription all use this copy, so their timing always matches exactly,
  whatever the original format.
- **File → Open Project… (Ctrl+Shift+O)** reopens a saved project
  (`.tsproj.json`): transcript, edits, speaker names, cleanup plan and sound
  tags, all without re-transcribing.
- A project exists as soon as a recording is open, so you can save a cleanup
  plan or sound tags before transcribing.
- If you open one of the app's own copies (`.ts.flac`, `.boosted.flac`), it
  opens the original it was made from instead.

## Playback and navigation

| Control | What it does |
|---|---|
| ▶ / ❚❚ or **Ctrl+Space** | Play / pause |
| ◀◀ ▶▶ or **Alt+Left / Alt+Right** | Skip back / forward. Set the step in the box, or click 5s / 10s / 30s |
| Position slider | Drag to move through the recording |
| Jump box or **Ctrl+J** | Type `1:02:03`, `2:03` or `95` and press Enter |
| Time display | Current position / total length |

Audio drives the transcript, never the other way round: the sentence being
played is highlighted in yellow, and the transcript scrolls to follow it
(untick **Follow playback** to stop the scrolling while you edit). Clicking in
the text never moves playback.

## The waveform

The waveform shows the recording's loudness over time.

- **Zoom:** mouse wheel (zooms around the pointer), or the **− / + / Fit**
  buttons.
- **Scroll:** Shift+wheel, or the scrollbar underneath. While playing, the
  view follows the red playhead.
- **Colours:** after transcription, each stretch is coloured like its text.
  That's the speaker's colour, **red** for low-confidence, and **gray** for
  non-speech sounds. Untranscribed audio is pale gray-blue.
- **Selecting a range:** drag across the waveform. The selection shows as a
  blue band with its start, end and length.
  - **Play** then starts at the selection and stops at its end; tick
    **Loop** to repeat it.
  - Drag either **edge** of the selection to adjust it (the pointer becomes
    ↔).
  - Click elsewhere, press **Esc**, or use **Clear selection** to remove it.
  - The words spoken in the selected range are shown **inverted** in the
    transcript, and the transcript scrolls to them.
- **Cleanup blocks** from the cleanup plan show as numbered amber bands just
  above the waveform. Click one to edit that block.
- **Words lane** (the **Words** checkbox): zoomed in to 40 seconds or less,
  each word is drawn under its own audio and scrolls with playback; the word
  being spoken is highlighted.

## Speech cleanup

The **Speech cleanup** tab helps you dig speech out of bad audio. It always
works from the *original* recording; your files are never changed.

### Controls

| Control | Use it for |
|---|---|
| **Preset** | Starting points: Normalize only, Gentle (good for transcription), Rumble & hiss, Telephone, Quiet far speaker, Hum removal, Heavy noise (RNNoise). Changing any control shows "Custom". |
| **Channel** | Stereo files: mix, left only, right only, or left − right (cancels noise that's equal in both channels) |
| **Low cut** | Removes rumble, handling noise, wind, air conditioning. 80–120 Hz for speech; up to 200 Hz for heavy rumble |
| **High cut** | Removes hiss and whine. 6–8 kHz is gentle; 4 kHz sounds like a telephone |
| **Hum notch** | Removes mains hum (60 Hz in North America, 50 Hz elsewhere) and its harmonics |
| **Noise reduction** | FFT: steady hiss or fan noise · Wavelet: hiss, keeps consonants crisp · RNNoise: changing noise such as traffic, chatter, keyboards · NL-means: smooth broadband noise (slow). Each one Light, Medium or Strong |
| **Presence** | Boosts around 3 kHz to make muffled or distant voices clearer |
| **Leveler** | Brings quiet speech up toward loud speech, e.g. one person near the phone and one across the room |
| **De-click / De-clip** | Repair clicks and pops / distorted peaks |
| **Normalize / Gain** | Overall loudness. A limiter always prevents clipping |

Hover over any control for a tip.

### Auditioning (A/B)

Select a range on the waveform and press **▶ Audition selection (A/B)**.

- Just that range is processed (in well under a second) and plays in a loop.
  The button turns blue: **Hearing PROCESSED**.
- Press the button again, or **Ctrl+B**, to flip between the processed and
  original sound at the same spot.
- Change any setting while it loops: it re-renders after a moment, so you
  can adjust by ear.

### The cleanup plan

Cleanup is organised as a **plan**, which is saved with the project.

- **Apply to: Selected range** + **Save to plan** adds a numbered *block*:
  that range with these settings. Blocks can't overlap.
- **Apply to: Whole file** + **Save to plan** sets the *base* settings used
  everywhere outside the blocks.
- To change an entry, **click it** in the Cleanup plan list (or click its
  amber band above the waveform). Its settings and range load back in.
  - Adjust the settings.
  - Adjust the range by dragging the selection edges or typing **Start /
    End**.
  - Press **Update block N**.
- **New block** starts a fresh one; **Delete** removes the entry you're
  editing.

### Rendering

**Render cleaned audio** writes one new file with the whole plan applied:

- the base settings everywhere,
- each block's settings in its own time range, blended in over 20 ms at
  each edge so there are no clicks.

The new file has exactly the same length and timing as the original, so the
transcript stays in sync. The note under the plan says whether the rendered
file is **up to date** or the plan has changed since.

After rendering:

- **Play rendered:** listen to the rendered file instead of the original.
- **Transcribe rendered:** transcribe the rendered file.
- **Save as new recording…:** save the rendered file under a normal name and
  open it as a fresh recording, for another round of cleanup.

> **Tip:** heavy cleanup can *sound* better but *transcribe* worse, because
> the speech recogniser was trained on noisy audio. Use **Gentle** for
> transcription and keep the heavy settings for listening.

## Transcribing

On the **Transcription** tab:

- **Engine**
  - **whisper.cpp (Vulkan GPU):** uses this computer's graphics card. This
    is the default.
  - **faster-whisper (NVIDIA CUDA):** picked automatically if an NVIDIA card
    is installed.
  - **faster-whisper (CPU):** a slower fallback.
- **Model:** larger models are more accurate but slower. `medium.en` is a
  good choice for English. Models download once, on first use.
- **Speakers:** *auto*, or the number of people talking, if you know it.
- **Speaker model:** **ECAPA** (accurate; the default) or **resemblyzer**
  (faster, weaker on similar voices).
- **Low-confidence below:** sentences whose average word confidence is
  below this are flagged 🔴.
- Click **Transcribe**. The progress area shows the step (1 of 6), a
  progress bar, time remaining, audio done, speed, GPU / CPU / memory use,
  and the latest transcribed line. **Cancel** stops it.

A 14-minute recording takes about 5–6 minutes with `medium.en` on this
computer's GPU.

## Reviewing and editing

- **Edit text:** click into any sentence and type. Enter is ignored; each
  sentence stays one line.
- **Change a paragraph's speaker:** use the dropdown next to its number.
  "Non-speech" is also an option there.
- **Low-confidence flag:** click the **○ / 🔴** button beside a sentence to
  set or clear it.
- **Right-click a sentence** for two audio shortcuts:
  - **Center waveform here** centres the waveform on the word you clicked.
  - **Select this sentence in the waveform** selects that sentence's audio,
    zoomed to fill two-thirds of the waveform. Then press Play (with Loop) to
    hear just that sentence.
- The window title shows `*` when there are unsaved changes.

## Participants and sound tags

### Naming participants

**Transcript → Name Participants… (Ctrl+Shift+N)** replaces "Participant 1,
2, …" with real names. Each row shows that person's talk time, when they
first speak, and their opening words, to help you tell who's who. Leave a
box empty to keep the number. Names appear in the speaker dropdowns and in
the export.

### Tagging sounds

1. Select a sound on the waveform that isn't speech (a sneeze, horn,
   bell…).
2. Click **Tag sound…** and type a word, or pick one from the list.

What happens next:

- The tag is saved with the project and shown as a purple box in the words
  lane.
- **With a transcript**, "(sneeze)" is inserted right away between the
  adjacent words.
- **Without one**, it's inserted when you transcribe, and again on every
  re-transcription.
- Annotations in (parentheses) or [brackets] show in *italics* in the editor
  and the export.
- To remove a tag, right-click its purple box. Its text is removed from the
  transcript too.

## Saving and exporting

- **Save Project (Ctrl+S) / Save Project As… (Ctrl+Shift+S):** the default
  is `<recording>.tsproj.json` next to the audio. If you move the project and
  the recording together, it still finds the audio.
- **File → Export… (Ctrl+E)**, or the **Export…** button, opens the Export
  dialog:
  - **Format:**
    - **Markdown (.md):** the standard colour-coded format, with headers,
      numbered paragraphs and sentences, legend, 🔴 low-confidence and ⚫
      non-speech markers.
    - **PDF (.pdf):** a paginated, printable document (Letter size) with
      the same colours.
    - **Word (.docx):** an editable document with headings, tables and
      coloured text.

    In PDF and Word the coloured emoji become coloured ● dots.
  - **Include file & transcription details:** adds or removes the header
    tables.
  - **Save to:** the full path. Type it or use **Browse…**. The dialog
    remembers your last folder, format and choice of details.
- With details included, the export has two tables:
  - **file details:** format, length, size, recording date and device,
    loudness, and the cleanup used;
  - **transcription details:** engine, model, time taken, speakers found,
    counts, confidence, and talk time per participant.

  Leave them out for the plain legacy format.

## Progress, logs and troubleshooting

- **Write log file** (Transcription tab, or the **Log** menu) records every
  step, command, timing and error. **View log** opens the current log; Log →
  Open log folder shows them all (`~/.local/state/transcript-studio/logs`).
  The last 30 are kept.
- If something fails, the error box says which step failed and has an
  **Open log** button.
- **A first run seems stuck:** a model may be downloading. The progress bar
  shows megabytes downloaded.
- **No sound / wrong position:** check the time display. If a selection is
  set, Play starts at the selection.
- **A phantom extra speaker:** reassign its paragraphs with the speaker
  dropdown, or set **Speakers** to the right number and transcribe again.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Ctrl+O | Open audio |
| Ctrl+Shift+O | Open project |
| Ctrl+S / Ctrl+Shift+S | Save project / Save as |
| Ctrl+E | Export (Markdown, PDF or Word) |
| Ctrl+Q | Quit |
| Ctrl+Space | Play / pause |
| Alt+Left / Alt+Right | Skip back / forward |
| Ctrl+J | Jump to a time |
| Ctrl+B | Audition A/B: processed ↔ original |
| Ctrl+Shift+N | Name participants |
| Esc (in the waveform) | Clear the selection |
| F1 | This manual |

## Files the app creates

| File | What it is |
|---|---|
| `<name>.ts.flac` | Playback copy (mono, lossless). Safe to delete; it's recreated when needed |
| `<name>.boosted.flac` | Rendered cleaned audio |
| `<name>.tsproj.json` | The project: transcript, edits, names, cleanup plan, sound tags |
| `<name>.md` / `.pdf` / `.docx` | Exported transcript |
| `~/.local/state/transcript-studio/logs/` | Log files |
