"""Evaluate diarization settings against every clip in samples/multispeaker.

    .venv/bin/python tools/eval_diarization.py            # default params
    .venv/bin/python tools/eval_diarization.py --sweep    # parameter grid

Transcripts are cached as samples/multispeaker/.cache/<clip>.tsproj.json
(created with the pipeline on first run) and embeddings as <clip>.<encoder>.npz,
so trying a new clustering setting takes seconds.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import warnings
from dataclasses import replace
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np  # noqa: E402

from score_diarization import score_labels  # noqa: E402
from transcript_studio import audio, diarize  # noqa: E402
from transcript_studio.model import Session  # noqa: E402
from transcript_studio.pipeline import PipelineOptions, run  # noqa: E402

CLIPS = ROOT / "samples" / "multispeaker"
CACHE = CLIPS / ".cache"


def cases():
    for ref in sorted(CLIPS.glob("*.reference.md")):
        name = ref.name.removesuffix(".reference.md")
        audio_file = next(p for p in CLIPS.glob(name + ".*") if p.suffix in {".mp3", ".wav", ".m4a", ".flac"}
                          and not p.name.endswith(audio.PLAYBACK_SUFFIX))
        yield name, audio_file, ref


def load_case(name: str, audio_file: Path, encoder: str = "resemblyzer"):
    CACHE.mkdir(exist_ok=True)
    proj = CACHE / f"{name}.tsproj.json"
    if not proj.exists():
        print(f"[{name}] transcribing (cached afterwards)…", file=sys.stderr)
        run(audio_file, PipelineOptions("medium.en")).save(proj)
    session = Session.load(proj)
    spans = [(s.start_time, s.end_time) for p, s in session.iter_sentences() if not p.is_nonspeech]
    emb_file = CACHE / f"{name}.{encoder}.npz"
    if emb_file.exists():
        data = np.load(emb_file)
        if data["spans"].shape == (len(spans), 2) and np.allclose(data["spans"], spans):
            embs = [None if np.isnan(e).any() else e for e in data["embs"]]
            return spans, embs
    pcm = audio.load_pcm(audio.make_playback_copy(audio_file))
    embs = diarize.embed_spans(pcm, spans, encoder)
    dim = next(e.shape[0] for e in embs if e is not None)
    np.savez(emb_file, spans=np.array(spans),
             embs=np.stack([e if e is not None else np.full(dim, np.nan) for e in embs]))
    return spans, embs


def evaluate(data, params, num_speakers=None):
    rows = []
    for name, (spans, embs, ref) in data.items():
        r = diarize.assign_speakers(embs, spans, num_speakers, params=params)
        s = score_labels([(a, b, lab) for (a, b), lab in zip(spans, r.labels)], ref)
        rows.append((name, s["accuracy"], s["predicted_speakers"], s["true_speakers"], r.detail))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--encoder", default=diarize.DEFAULT_ENCODER, choices=list(diarize.ENCODERS))
    args = ap.parse_args()
    data = {}
    for name, audio_file, ref in cases():
        spans, embs = load_case(name, audio_file, args.encoder)
        data[name] = (spans, embs, ref)

    if not args.sweep:
        for name, acc, k, true_k, detail in evaluate(data, diarize.PARAMS_BY_ENCODER[args.encoder]):
            print(f"{name:28s} acc {acc:6.1%}  K {k}/{true_k}  {detail}")
        return

    grid = itertools.product([0.08, 0.12, 0.2], [0.80, 0.85, 0.90, 1.0], [0.0, 0.03, 0.05, 0.08],
                             ["average", "complete"], [1.0, 1.5, 2.0])
    results = []
    for sil, sim, share, link, rel in grid:
        p = replace(diarize.DEFAULT_PARAMS, min_silhouette=sil, max_centroid_similarity=sim,
                    min_cluster_share=share, linkage=link, reliable_seconds=rel)
        rows = evaluate(data, p)
        k_ok = sum(k == tk for _, _, k, tk, _ in rows)
        results.append((min(r[1] for r in rows), np.mean([r[1] for r in rows]), k_ok, p, rows))
    results.sort(key=lambda r: (r[2], r[0], r[1]), reverse=True)
    for worst, mean, k_ok, p, rows in results[:10]:
        print(f"worst {worst:6.1%} mean {mean:6.1%} K-correct {k_ok}/{len(rows)}  sil>={p.min_silhouette} "
              f"sim<={p.max_centroid_similarity} share>={p.min_cluster_share} {p.linkage} rel>={p.reliable_seconds}")
        print("    " + "  ".join(f"{n.split('-')[-1]}:{a:.0%}(K{k})" for n, a, k, _, _ in rows))


if __name__ == "__main__":
    main()
