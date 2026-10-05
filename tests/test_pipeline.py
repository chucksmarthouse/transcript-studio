from transcript_studio.backends import RawSegment, _wcpp_segment
from transcript_studio.model import Word
from transcript_studio.pipeline import _group_paragraphs, normalize_annotation, split_segment


def seg(text, start=0.0, step=0.5, p=0.9):
    words = [Word(w, start + i * step, start + (i + 1) * step, p) for i, w in enumerate(text.split())]
    return RawSegment(start, start + len(words) * step, text, words)


def test_annotation_forms_normalised():
    assert normalize_annotation("*coughing*") == "(coughing)"
    assert normalize_annotation("(laughs)") == "(laughs)"
    assert normalize_annotation("[inaudible]") == "[inaudible]"


def test_annotation_split_out_of_speech():
    units = split_segment(seg("(laughs) Okay so that's it. [door closes] Right"))
    assert [(u.text, u.nonspeech) for u in units] == [
        ("(laughs)", True), ("Okay so that's it.", False), ("[door closes]", True), ("Right", False)]


def test_blank_audio_dropped():
    assert split_segment(seg("[BLANK_AUDIO]")) == []


def test_sentence_split_keeps_ellipsis_and_abbreviations():
    units = split_segment(seg("I met Dr. Smith today... it went fine. Did it? Yes!"))
    assert [u.text for u in units] == ["I met Dr. Smith today... it went fine.", "Did it?", "Yes!"]


def test_unclosed_paren_is_speech():
    assert [u.nonspeech for u in split_segment(seg("(well I think so"))] == [False]


def test_paragraph_breaks_on_speaker_gap_and_nonspeech():
    units = (split_segment(seg("Hello there.", 0)) + split_segment(seg("How are you.", 1.0))
             + split_segment(seg("Fine.", 5.0)) + split_segment(seg("(coughing)", 5.6)))
    for u, spk in zip(units, [1, 1, 1, None]):
        u.speaker = spk
    paras = _group_paragraphs(units)
    # gap 1.0 -> same paragraph; gap >= 2s -> new; non-speech -> its own paragraph
    assert [len(p.sentences) for p in paras] == [2, 1, 1]
    assert paras[-1].is_nonspeech


def test_wcpp_tokens_merge_into_words():
    raw = {"offsets": {"from": 0, "to": 1000}, "text": " Hello world", "tokens": [
        {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "p": 0.5},
        {"text": " Hel", "offsets": {"from": 0, "to": 200}, "p": 0.8},
        {"text": "lo", "offsets": {"from": 200, "to": 400}, "p": 0.6},
        {"text": " world", "offsets": {"from": 400, "to": 1000}, "p": 0.9},
    ]}
    s = _wcpp_segment(raw)
    assert [(w.text, w.start_time, w.end_time) for w in s.words] == [("Hello", 0.0, 0.4), ("world", 0.4, 1.0)]
    assert abs(s.words[0].probability - 0.7) < 1e-9
