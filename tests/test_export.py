"""Round-trip the reference transcript through Session -> markdown; must be byte-identical."""

import re
from pathlib import Path

from transcript_studio.export import to_markdown
from transcript_studio.model import Paragraph, Sentence, Session

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "samples" / "multispeaker" / "nasa-podcast-ep434-2speakers.format-reference.md"

HEADER_RE = re.compile(r"^### (\d+)\. (\S+) (?:Participant (\d+) — )?_(\d\d):(\d\d):(\d\d)_$")
SENT_RE = re.compile(r'^\*\*(\d+)\.(\d+)\*\* (?:(🔴|⚫) )?<span style="color:#[0-9a-f]{6}">(.*)</span>(  )?$')


def parse_reference(text: str) -> Session:
    title = re.match(r"^# (.*) Transcript$", text.splitlines()[0]).group(1)
    model = re.search(r"model: ([^)]+)\)", text).group(1)
    session = Session(audio_path="nasa-podcast-ep434-2speakers.mp3", model_size=model, title=title)
    para = None
    for line in text.splitlines():
        if m := HEADER_RE.match(line):
            h, mi, s = int(m[4]), int(m[5]), int(m[6])
            para = Paragraph(
                index=int(m[1]),
                speaker_id=int(m[3]) if m[3] else None,
                is_nonspeech=m[3] is None,
                start_time=h * 3600 + mi * 60 + s,
            )
            session.paragraphs.append(para)
        elif m := SENT_RE.match(line):
            body = m[4]
            if para.is_nonspeech:
                body = body[1:-1]  # strip the _italic_ markers
            para.sentences.append(Sentence(index=int(m[2]), text=body, low_confidence=m[3] == "🔴"))
    return session


def test_reference_round_trip():
    ref = REFERENCE.read_text(encoding="utf-8")
    session = parse_reference(ref)
    assert len(session.paragraphs) == 19
    assert to_markdown(session, details=False) == ref


def test_json_round_trip(tmp_path):
    session = parse_reference(REFERENCE.read_text(encoding="utf-8"))
    session.save(tmp_path / "p.json")
    assert Session.load(tmp_path / "p.json") == session


def test_legend_lists_only_present_speakers():
    session = parse_reference(REFERENCE.read_text(encoding="utf-8"))
    for para in session.paragraphs:  # fold Participant 2 into 3
        if para.speaker_id == 2:
            para.speaker_id = 3
    md = to_markdown(session, details=False)
    assert "Participant 1</span>" in md and "Participant 3</span>" in md
    assert "Participant 2</span>" not in md
    assert "### 3. 🟠 Participant 3 — _00:00:02_" in md


def test_save_tolerates_numpy_values(tmp_path):
    import numpy as np
    from transcript_studio.model import Word
    session = parse_reference(REFERENCE.read_text(encoding="utf-8"))
    sent = session.paragraphs[1].sentences[0]
    sent.low_confidence = np.bool_(True)
    sent.words = [Word("When", np.float64(7.0), np.float32(7.4), np.float32(0.9))]
    session.save(tmp_path / "p.json")
    loaded = Session.load(tmp_path / "p.json")
    assert loaded.paragraphs[1].sentences[0].low_confidence is True
    assert abs(loaded.paragraphs[1].sentences[0].words[0].probability - 0.9) < 1e-6


def test_details_header_is_inserted_without_changing_the_rest(monkeypatch):
    import time
    monkeypatch.setenv("TZ", "America/Denver")  # dates are shown in local time
    time.tzset()
    session = parse_reference(REFERENCE.read_text(encoding="utf-8"))
    session.info = {"audio": {"file": "x.m4a", "codec": "AAC (Advanced Audio Coding)", "codec_name": "aac", "codec_profile": "HE-AAC", "container": "MP4",
                              "sample_rate": 48000, "channels": 2, "bit_rate": 48000, "duration": 852.3,
                              "size_bytes": 5367611, "created": "2026-10-01T22:58:20Z", "device": "Android 14",
                              "loudness": {"integrated_lufs": -29.7, "true_peak_dbtp": -0.1}},
                    "transcription": {"engine": "whisper.cpp (Vulkan GPU)", "started": "2026-10-03T19:00:00-06:00",
                                      "processing_seconds": 290, "audio_seconds": 852.3, "speaker_model": "ecapa",
                                      "speakers_requested": None, "speakers_found": 1, "source": "original"}}
    plain, full = to_markdown(session, details=False), to_markdown(session)
    assert "| Format | AAC (HE-AAC), .m4a file |" in full
    assert "| Recorded | 2026-10-01 16:58 MDT · Android 14 |" in full  # shown in local time
    assert "| Transcribed | 2026-10-03 19:00 MDT · took 00:04:50 (2.9× real time) |" in full
    assert "| Speaker separation | ecapa, auto → 1 found |" in full
    assert "| 🔵 Participant 1 |" in full
    # Removing the two tables gives back the legacy-exact output.
    start = full.index("**File details**")
    end = full.index("**Legend:**")
    assert full[:start] + full[end:] == plain


def test_participant_names_in_export_and_project(tmp_path):
    session = parse_reference(REFERENCE.read_text(encoding="utf-8"))
    session.speaker_names = {"1": "Host", "2": "Guest <G>"}
    md = to_markdown(session, details=False)
    assert "### 2. 🔵 Host — _00:00:00_" in md
    assert "### 3. 🟢 Guest <G> — _00:00:02_" in md
    assert '- 🔵 <span style="color:#1d4ed8">Host</span>' in md
    assert '- 🟢 <span style="color:#059669">Guest &lt;G&gt;</span>' in md  # escaped inside the span
    assert "Participant 1" not in md and "Participant 2" not in md
    session.save(tmp_path / "p.json")
    assert Session.load(tmp_path / "p.json").speaker_label(2) == "Guest <G>"
    session.speaker_names = {"1": "  "}  # blank -> number
    assert session.speaker_label(1) == "Participant 1"
