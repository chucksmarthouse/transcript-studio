"""Build a markdown format reference (spec §8) from a transcript project file.

    .venv/bin/python tools/make_format_reference.py PROJECT.tsproj.json OUT.md --title "NASA Podcast Ep 434 Excerpt"

The reference exercises every markup type: a made-up leading "(music)" non-speech paragraph
and low-confidence flags on the first two sentences of paragraph 3 are added to the project's
real transcript. Tests round-trip it, so it pins the export format, not the wording.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transcript_studio.export import to_markdown  # noqa: E402
from transcript_studio.model import Paragraph, Sentence, Session  # noqa: E402


def build(project: Path, title: str) -> Session:
    session = Session.load(project)
    session.title = title
    session.audio_path = Path(session.audio_path).name
    session.engine = "faster-whisper"
    session.speaker_names = {}
    session.paragraphs.insert(0, Paragraph(index=1, speaker_id=None, is_nonspeech=True, start_time=0,
                                           sentences=[Sentence(index=i, text="(music)") for i in (1, 2, 3)]))
    for para in session.paragraphs:
        for sent in para.sentences:
            sent.words = []
            sent.low_confidence = False
    session.renumber()
    for sent in session.paragraphs[2].sentences[:2]:
        sent.low_confidence = True
    return session


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--title", required=True)
    args = ap.parse_args()
    args.out.write_text(to_markdown(build(args.project, args.title), details=False), encoding="utf-8")
    print(f"wrote {args.out}")
