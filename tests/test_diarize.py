"""Diarization regression tests against samples/multispeaker ground truth.

Uses the transcripts/embeddings cached by tools/eval_diarization.py; those
cases are skipped if the cache hasn't been built yet.
"""

import sys

import numpy as np
import pytest

from tests.test_export import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from eval_diarization import CACHE, cases, evaluate, load_case  # noqa: E402

from transcript_studio import diarize  # noqa: E402


@pytest.mark.parametrize("encoder", ["ecapa", "resemblyzer"])
def test_speaker_count_and_accuracy(encoder):
    data = {}
    for name, audio_file, ref in cases():
        if not (CACHE / f"{name}.{encoder}.npz").exists():
            pytest.skip(f"run tools/eval_diarization.py --encoder {encoder} first")
        spans, embs = load_case(name, audio_file, encoder)
        data[name] = (spans, embs, ref)
    assert len(data) >= 1
    for name, acc, k, true_k, detail in evaluate(data, diarize.PARAMS_BY_ENCODER[encoder]):
        assert k == true_k, f"{name}: found {k} speakers, expected {true_k} ({detail})"
        assert acc >= 0.95, f"{name}: accuracy {acc:.1%} ({detail})"


def test_falls_back_to_resemblyzer_when_ecapa_unavailable(monkeypatch):
    def broken(chunk):
        raise ImportError("no speechbrain")

    monkeypatch.setitem(diarize.ENCODERS, "ecapa", broken)
    rng = np.random.default_rng(0)
    pcm = (rng.standard_normal(16000 * 12) * 0.1).astype(np.float32)
    r = diarize.diarize(pcm, [(0, 3), (3, 6), (6, 9), (9, 12)])
    assert r.detail.startswith("resemblyzer:") and ("ecapa unavailable" in r.detail or "ecapa failed" in r.detail)
    assert len(r.labels) == 4


def test_fold_small_clusters():
    X = np.array([[1, 0], [1, 0.05], [0, 1], [0, 1.05], [0.7, 0.7]], dtype=float)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    labels = np.array([0, 0, 1, 1, 2])
    w = np.array([10, 10, 10, 10, 0.5])
    out, folded = diarize._fold_small_clusters(X, labels, w, 0.05)
    assert folded == 1 and len(set(out)) == 2
