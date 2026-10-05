"""Headless pipeline runner, for testing without the GUI.

    .venv/bin/python -m transcript_studio.cli samples/multispeaker/nasa-podcast-ep434-2speakers.mp3 -m medium.en -o out.md
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

from .audio import BoostSettings, boost, boosted_path
from .backends import BACKENDS, best_backend, get_backend
from .export_formats import export
from .pipeline import PipelineOptions, run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("-m", "--model", default="medium.en")
    ap.add_argument("-b", "--backend", choices=[b.key for b in BACKENDS], help="default: best available")
    ap.add_argument("-k", "--speakers", type=int, help="expected speaker count (default: auto)")
    ap.add_argument("--speaker-model", choices=["ecapa", "resemblyzer"], help="default: ecapa")
    ap.add_argument("--threshold", type=float, default=0.5, help="low-confidence threshold")
    ap.add_argument("--boost", action="store_true", help="loudnorm-boost the audio before transcribing")
    ap.add_argument("--title")
    ap.add_argument("-o", "--output", help="output file: .md, .pdf or .docx (default: <audio>.md)")
    ap.add_argument("--no-details", action="store_true", help="leave out the file & transcription details header")
    ap.add_argument("-p", "--project", help="also save a JSON project file")
    ap.add_argument("--log", action="store_true", help="also write a log file")
    args = ap.parse_args(argv)
    if args.log:
        from .logs import set_file_logging
        print(f"Log: {set_file_logging(True)}", file=sys.stderr)

    backend = get_backend(args.backend) if args.backend else best_backend()
    ok, why = backend.available()
    if not ok:
        sys.exit(f"{backend.label}: {why}")
    print(f"Backend: {backend.label}", file=sys.stderr)

    boosted = None
    if args.boost:
        boosted = boost(args.audio, boosted_path(args.audio), BoostSettings())

    last = [None]

    def on_event(e):
        pct = "…" if e.fraction is None else f"{e.fraction:4.0%}"
        line = f"[{e.step}/{e.steps}] {e.name}: {pct} {e.detail}"
        if line != last[0]:
            last[0] = line
            print(f"\r\033[K{line[:150]}", end="", file=sys.stderr, flush=True)

    t0 = time.time()
    from .logs import log
    try:
        session = run(args.audio, PipelineOptions(args.model, backend=backend, num_speakers=args.speakers,
                                              low_confidence_threshold=args.threshold,
                                              speaker_model=args.speaker_model),
                  on_event, boosted_audio_path=boosted)
    except Exception:
        log.exception("CLI transcription failed")
        raise
    if args.title:
        session.title = args.title
    print(f"\nDone in {time.time() - t0:.1f}s — {len(session.paragraphs)} paragraphs, "
          f"speakers {session.speaker_ids()} ({session.diarization_detail})", file=sys.stderr)

    out = args.output or str(Path(args.audio).with_suffix(".md"))
    export(session, out, details=not args.no_details)
    print(f"Wrote {out}", file=sys.stderr)
    if args.project:
        session.save(args.project)
        print(f"Wrote {args.project}", file=sys.stderr)


if __name__ == "__main__":
    main()
