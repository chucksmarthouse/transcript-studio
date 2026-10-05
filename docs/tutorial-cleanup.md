# Tutorial: cleaning up a bad recording

You'll take a recording with four problems, find each one by ear and on the waveform, fix it
with the **Speech cleanup** tab, and check the result with numbers. It takes about 20 minutes.

The file is `samples/tutorial/nasa-podcast-ep434-needs-cleanup.mp3`: a clean NASA podcast clip
(4:37, two speakers) with these problems added on purpose:

| # | Problem | Where | Sounds like |
|---|---|---|---|
| 1 | Clipped passage | 1:40–2:00 | harsh, buzzy distortion |
| 2 | Low overall level | whole file | you have to turn the volume up |
| 3 | 60 Hz hum | whole file | a steady low drone |
| 4 | Background hiss | whole file | a constant "shhh" under the voices |

`samples/tutorial/nasa-podcast-ep434-needs-cleanup.answer-key.json` records exactly what was
added, if you want to peek.

## 0. Before you start

Open a terminal in the project folder and measure the file. `tools/tutorial_metrics.py` prints
four numbers that you'll use to check your work:

```
.venv/bin/python tools/tutorial_metrics.py samples/tutorial/nasa-podcast-ep434-needs-cleanup.mp3 --clip 100 120
```

| Number | What it tells you | This file | The clean original |
|---|---|---|---|
| `hum_db` | How far a 60 Hz hum sticks out of its surroundings (dB) | about 34 | about 0 |
| `snr_db` | How far speech stands above the background (dB, higher is cleaner) | about 11 | about 36 |
| `near_peak_fraction` | Share of samples piled up at the peak: high means clipping | about 0.17 | about 0.008 |
| `integrated_lufs` | Overall loudness (LUFS; quieter is more negative) | about -35 | about -13.5 |

## 1. Open the file and listen

1. Start the app and choose **File → Open Audio… (Ctrl+O)**, then pick the tutorial MP3.
2. Press **Ctrl+Space** and listen for a minute. You should hear the drone and hiss under the
   voices, and the speakers are quiet.
3. Jump to 1:40 with **Ctrl+J**, type `100` and press Enter. Listen to the next 20 seconds: the
   voices turn harsh. That's the clipped passage.
4. Look at the waveform. Most of the file is a thin band, because it's quiet. The clipped
   passage looks flat on top, as if its peaks had been sliced off.

<!-- screenshot: waveform with the 1:40–2:00 passage selected -->

## 2. Fix the hum (and the rumble under it)

1. Drag across a few seconds on the waveform to select a range, for example 0:20–0:30.
2. Open the **Speech cleanup** tab and choose the **Hum removal** preset. It cuts below 70 Hz
   and notches 60 Hz and its harmonics.
3. Press **▶ Audition selection (A/B)**. The button turns blue: **Hearing PROCESSED**. Press
   **Ctrl+B** a few times to flip between processed and original. The drone should vanish.

If you live where mains power is 50 Hz, the **Hum notch** setting is where you'd change it. For
this file it's 60 Hz.

## 3. Fix the hiss

1. Keep the range selected and the **Hum removal** preset's settings. Set **Noise reduction** to
   **FFT** at **Medium**.
2. Audition again. Try **Light**, **Medium** and **Strong**: stronger removes more hiss but makes
   the voices thinner and more "watery". **Medium** is the compromise this tutorial uses.
3. Optionally compare with **Wavelet**. It keeps consonants a little crisper on some voices.

<!-- screenshot: the Speech cleanup tab with Hum removal + FFT Medium -->

## 4. Build the cleanup plan

Cleanup is organised as a plan that's saved with the project (see *The cleanup plan* in the user
manual).

1. Choose **Apply to: Whole file** and press **Save to plan**. The hum and hiss settings, plus
   **Normalize**, now apply everywhere. Normalize fixes the low level (problem 2): it brings the
   loudness up to about -17 LUFS (the normaliser aims at -16 and lands just under), close to a normal podcast.
2. Select the clipped passage. Type **Start** `100` and **End** `120`, or drag the selection
   edges. Turn on **De-clip** (keep the other settings the same), choose **Apply to: Selected
   range**, and press **Save to plan**. An amber block "1" appears above the waveform.
3. Audition the block: the harsh distortion should be much smoother. De-clip rebuilds the
   flattened peaks; it can't make them perfect.

## 5. Render and measure

1. Press **Render cleaned audio**. When it finishes, the note under the plan says **up to date**.
2. Press **Play rendered** and listen from the start. Same recording, same timing, now clean and
   even.
3. Use **Save as new recording…**, for example `cleaned.flac`, then measure it in the terminal:

```
.venv/bin/python tools/tutorial_metrics.py cleaned.flac --clip 100 120
```

You should see numbers close to these (they depend slightly on your choices):

| Number | Before | After the recommended cleanup |
|---|---|---|
| `hum_db` | 34.3 | -5.7 |
| `snr_db` | 11.4 | 24.1 |
| `near_peak_fraction` | 0.172 | 0.009 |
| `integrated_lufs` | -35.2 | -17.1 |

Hum is gone (the number is below zero because the notch leaves a small dip), speech stands out
further from the background, the clipped passage is back to a normal amount of peak, and the
loudness is where podcasts usually sit. The hiss is reduced, not eliminated: `snr_db` goes
from 11 to 24, while the clean original was 36.

## 6. Transcribe both and compare

1. On the **Transcription** tab, transcribe the original tutorial file and save the project.
2. Open the cleaned file and transcribe it too.
3. Compare the two. **Expect small differences.** The source clip is already clean, so even the
   degraded version transcribes reasonably well. The text of the clipped passage and the quiet
   stretches is where you may see a few word changes. Cleanup mainly makes the recording nicer
   to listen to and review.

> **Tip:** heavy cleanup can sound better but transcribe worse, because the speech recogniser
> was trained on noisy audio. For transcription, **Gentle** settings are usually enough, and the
> heavy settings are for listening.

## Where to go next

- Try the other presets on this file and watch what the numbers do.
- Change a setting you didn't touch, such as **Presence** or **Leveler**, and see which
  measure moves.
- Make your own bad recording with `tools/make_tutorial_sample.py --src your-clean-file.mp3`.
  The constants at the top of that script set where and how hard each problem is added.
