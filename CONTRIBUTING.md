# Contributing

- Set up as in the README, then run the tests with `.venv/bin/python -m pytest -q`. No GPU is
  needed: the tests use ffmpeg and the bundled sample.
- Diarization tests skip until you build their cache:
  `.venv/bin/python tools/eval_diarization.py --encoder ecapa` (and `--encoder resemblyzer`).
- Regenerate the tutorial sample with `.venv/bin/python tools/make_tutorial_sample.py`. It is
  deterministic for a given ffmpeg version; `tests/test_tutorial_sample.py` checks it still has every defect and that the
  recommended cleanup still fixes them.
- Before publishing, run `bash tools/release_check.sh`.
