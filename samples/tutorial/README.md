# Tutorial sample: needs cleanup

`nasa-podcast-ep434-needs-cleanup.mp3` is the NASA ep. 434 clip (see `../multispeaker/README.md`)
with four defects added on purpose, for `docs/tutorial-cleanup.md`:

| Defect | Where | Added by |
|---|---|---|
| Clipped passage | 1:40–2:00 | +14 dB boost, hard-clipped |
| Low level | whole file | lowered to about -35 LUFS |
| 60 Hz hum | whole file | 60 Hz plus 120 and 180 Hz |
| Background hiss | whole file | white noise at -52 dBFS |

Regenerate with `.venv/bin/python tools/make_tutorial_sample.py` (deterministic for a given ffmpeg version).
`*.answer-key.json` records what was applied and the recommended cleanup plan. Measure any file
with `.venv/bin/python tools/tutorial_metrics.py FILE --clip 100 120`.

| Measure | Clean source | This file | After the recommended cleanup |
|---|---|---|---|
| `hum_db` | 0.4 | 34.3 | -5.7 |
| `snr_db` | 35.8 | 11.4 | 24.1 |
| `near_peak_fraction` (100–120 s) | 0.008 | 0.172 | 0.009 |
| `integrated_lufs` | -13.5 | -35.2 | -17.1 |
