from transcript_studio.export import to_markdown
from transcript_studio.model import Paragraph, Sentence, Session, Word
from transcript_studio.sounds import apply_all, insert_tag, italicize_annotations, tag_text


def _session():
    def sent(i, words):
        ws = [Word(t, a, b, 0.9) for t, a, b in words]
        return Sentence(i, " ".join(w.text for w in ws), words=ws, start_time=ws[0].start_time, end_time=ws[-1].end_time)
    s = Session(audio_path="x.m4a", model_size="m")
    s.paragraphs = [
        Paragraph(1, 1, False, 1.0, [sent(1, [("I", 1.0, 1.2), ("was", 1.3, 1.5), ("going.", 1.6, 2.0)]),
                                     sent(2, [("Then", 3.0, 3.3), ("stop.", 3.4, 3.8)])]),
        Paragraph(2, 2, False, 6.0, [sent(1, [("Okay.", 6.0, 6.4)])]),
    ]
    return s


def test_tag_text():
    assert tag_text("sneeze") == "(sneeze)" and tag_text(" (horn) ") == "(horn)"


def test_insert_between_words_keeps_word_alignment():
    s = _session()
    para, sent = insert_tag(s, 1.52, 1.58, "sneeze")
    assert sent.text == "I was (sneeze) going."
    assert [w.text for w in sent.words] == ["I", "was", "(sneeze)", "going."]
    assert len(sent.token_times()) == len(sent.words)


def test_insert_at_sentence_boundary_and_start():
    s = _session()
    insert_tag(s, 2.4, 2.8, "horn")  # between sentences: after the last word before it
    assert s.paragraphs[0].sentences[0].text == "I was going. (horn)"
    assert s.paragraphs[0].sentences[0].end_time == 2.8
    insert_tag(s, 0.2, 0.5, "bell")  # before everything
    assert s.paragraphs[0].sentences[0].text.startswith("(bell) I was")
    assert s.paragraphs[0].sentences[0].start_time == 0.2


def test_insert_into_edited_text_falls_back_to_time_proportion():
    s = _session()
    s.paragraphs[0].sentences[1].text = "Then we stopped right there."  # edited: 5 tokens vs 2 words
    insert_tag(s, 3.5, 3.6, "door")
    assert "(door)" in s.paragraphs[0].sentences[1].text


def test_apply_all_after_transcription():
    s = _session()
    s.sound_tags = [{"start": 6.5, "end": 7.0, "label": "applause"}, {"start": 1.52, "end": 1.58, "label": "sneeze"}]
    assert apply_all(s) == 2
    assert s.paragraphs[1].sentences[0].text == "Okay. (applause)"


def test_export_italicizes_sound_tags():
    s = _session()
    insert_tag(s, 1.52, 1.58, "sneeze")
    md = to_markdown(s, details=False)
    assert '<span style="color:#1d4ed8">I was _(sneeze)_ going.</span>' in md
    assert italicize_annotations("a [inaudible] b") == "a _[inaudible]_ b"
