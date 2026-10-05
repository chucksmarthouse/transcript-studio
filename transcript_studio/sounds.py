"""User-tagged sound events ("sneeze", "horn", "bell"...) in the transcript.

A tag is stored on the session ({"start", "end", "label"}) and inserted into
the transcript text as "(label)" between the two words where it happened —
right away if there is a transcript, or after the next transcription.
Parenthesised/bracketed annotations in speech text are shown and exported in
italics (the same convention as Whisper's own "(coughing)").
"""

from __future__ import annotations

import re

from .logs import log
from .model import Session, Word

ANNOTATION_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
SUGGESTIONS = ["sneeze", "cough", "laughs", "sigh", "horn", "bell", "door", "phone ringing", "dog barking",
               "music", "applause", "crosstalk", "inaudible", "background noise"]


def tag_text(label: str) -> str:
    label = label.strip().strip("()[]").strip()
    return f"({label})"


def insert_tag(session: Session, start: float, end: float, label: str):
    """Insert "(label)" between the words around `start`. Returns (paragraph, sentence) or None."""
    text = tag_text(label)
    # Every token in transcript order with its sentence: insert after the last one that
    # starts before the tag (or before the very first token).
    last = None
    for para in session.paragraphs:
        for sent in para.sentences:
            for k, (a, b, t0, _t1) in enumerate(sent.token_times()):
                if t0 < start:
                    last = (para, sent, k, b)
    if last is None:
        if not session.paragraphs or not session.paragraphs[0].sentences:
            return None
        para, sent = session.paragraphs[0], session.paragraphs[0].sentences[0]
        k, pos = -1, 0
    else:
        para, sent, k, pos = last

    aligned = len(sent.words) == len(sent.token_times())
    at_end = pos >= len(sent.text.rstrip())
    if pos == 0:
        sent.text = f"{text} {sent.text}".strip()
    else:
        sent.text = f"{sent.text[:pos]} {text}{sent.text[pos:]}"
    if aligned:  # keep word timestamps lined up with the text's tokens
        sent.words.insert(k + 1, Word(text, start, end, 1.0))
    if pos == 0:
        sent.start_time = min(sent.start_time, start)
    if at_end:
        sent.end_time = max(sent.end_time, end)
    log.info("Sound tag %s at %.2f-%.2fs inserted into %d.%d", text, start, end, para.index, sent.index)
    return para, sent


def apply_all(session: Session) -> int:
    """Insert every stored tag (after a fresh transcription). Returns how many were inserted."""
    n = 0
    for tag in sorted(session.sound_tags, key=lambda t: t["start"]):
        if insert_tag(session, tag["start"], tag["end"], tag["label"]):
            n += 1
    return n


def italicize_annotations(text: str) -> str:
    """Markdown italics for "(...)" / "[...]" annotations inside speech text."""
    return ANNOTATION_RE.sub(lambda m: f"_{m.group(0)}_", text)


def remove_tag(session: Session, index: int) -> bool:
    """Delete stored tag `index` and its "(label)" text nearest its time. True if text was removed."""
    tag = session.sound_tags.pop(index)
    text = tag_text(tag["label"])
    best = None  # (time distance, sentence, token index, char start, char end)
    for para in session.paragraphs:
        for sent in para.sentences:
            for k, (a, b, t0, _t1) in enumerate(sent.token_times()):
                if sent.text[a:b] == text:
                    d = abs(t0 - tag["start"])
                    if best is None or d < best[0]:
                        best = (d, sent, k, a, b)
    if best is None:
        return False
    _, sent, k, a, b = best
    aligned = len(sent.words) == len(sent.token_times())
    sent.text = (sent.text[:a].rstrip() + " " + sent.text[b:].lstrip()).strip()
    if aligned:
        del sent.words[k]
    log.info("Sound tag %s at %.2fs removed", text, tag["start"])
    return True
