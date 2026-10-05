# Sample data

Public-domain NASA clips only; files are named after their source.

| File | Source |
|---|---|
| `multispeaker/nasa-podcast-ep434-2speakers.mp3` | NASA podcast clip, two speakers. The end-to-end test file. See `multispeaker/README.md`. |
| `multispeaker/nasa-podcast-ep434-2speakers.reference.md` | NASA's speaker-labelled transcript: ground truth for diarization testing. |
| `multispeaker/nasa-podcast-ep434-2speakers.format-reference.md` | The clip exported in the spec §8 markdown format; ground truth for export *structure*. |
| `multispeaker/nasa-podcast-ep434-2speakers.tsproj.json` | A transcribed project for the clip, to open in the app. |
| `tutorial/nasa-podcast-ep434-needs-cleanup.mp3` | The same clip with four defects added on purpose, for `docs/tutorial-cleanup.md`. See `tutorial/README.md`. |

`*.ts.flac` files are the app's own playback copies (mono FLAC, created when a
file is opened). They can be deleted and are recreated on demand.
`multispeaker/.cache/` holds the transcripts and voice embeddings cached by
`tools/eval_diarization.py`.
