"""Speaker diarization: speaker embeddings + agglomerative clustering (spec §5.6, §6).

Two embedding models, neither needing a token or a gated model:
- ``resemblyzer``: weights ship inside the pip package.
- ``ecapa``: speechbrain/spkrec-ecapa-voxceleb, ungated, downloaded once to
  ~/.cache (the spec §6 fallback).
Tuned against NASA clips (tools/eval_diarization.py); the repository ships only one of them.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE
from .logs import log
from .progress import ProgressFn, null_progress

with warnings.catch_warnings():  # webrtcvad's pkg_resources deprecation noise
    warnings.simplefilter("ignore")
    from resemblyzer import VoiceEncoder, preprocess_wav

from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score

MIN_EMBED_SECONDS = 0.5


@dataclass
class DiarizeParams:
    # Spans shorter than this give noisy embeddings: they don't *form* the
    # clusters, they're assigned to the nearest one afterwards.
    reliable_seconds: float = 1.5
    # Auto speaker count: a K>1 split is accepted only if it is well separated.
    min_silhouette: float = 0.12
    max_centroid_similarity: float = 0.80
    # Clusters holding less than this share of the reliable speech are folded
    # into their nearest neighbour (0 = never).
    min_cluster_share: float = 0.0
    linkage: str = "average"


DEFAULT_PARAMS = DiarizeParams()
# Tuned per model on NASA podcast clips, 1-3 speakers, of which only the 2-speaker clip is
# distributed (tools/eval_diarization.py --sweep).
# ECAPA separates voices so clearly that every setting in the sweep was right;
# resemblyzer needs a looser similarity guard plus small-cluster folding.
PARAMS_BY_ENCODER = {
    "ecapa": DEFAULT_PARAMS,
    "resemblyzer": DiarizeParams(max_centroid_similarity=0.90, min_cluster_share=0.03),
}
DEFAULT_ENCODER = "ecapa"


@dataclass
class DiarizationResult:
    labels: list[int]  # 1-based speaker per input span, numbered by first appearance
    num_speakers: int
    detail: str


ECAPA_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "transcript-studio" / "spkrec-ecapa-voxceleb"
_encoders: dict = {}


def _resemblyzer_embed(chunk: np.ndarray) -> np.ndarray:
    if "resemblyzer" not in _encoders:
        _encoders["resemblyzer"] = VoiceEncoder(device="cpu", verbose=False)  # small; CPU is plenty fast
    wav = preprocess_wav(chunk, source_sr=SAMPLE_RATE)  # normalise + trim silences
    if len(wav) < MIN_EMBED_SECONDS * SAMPLE_RATE:
        wav = chunk  # trimming ate it; embed the untrimmed slice instead
    return _encoders["resemblyzer"].embed_utterance(wav)


def _load_ecapa(progress: ProgressFn = null_progress) -> None:
    if "ecapa" in _encoders:
        return
    from . import hub
    local = hub.download(ECAPA_SOURCE, "speaker model (ECAPA)", progress)
    progress(None, "Loading speaker model (ECAPA)…")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from speechbrain.inference.speaker import EncoderClassifier
        _encoders["ecapa"] = EncoderClassifier.from_hparams(
            source=local, savedir=str(ECAPA_DIR), run_opts={"device": "cpu"})
    log.info("ECAPA speaker model loaded from %s", local)


def _ecapa_embed(chunk: np.ndarray) -> np.ndarray:
    import torch
    _load_ecapa()
    with torch.no_grad():
        e = _encoders["ecapa"].encode_batch(torch.from_numpy(chunk).float().unsqueeze(0))
    e = e.squeeze().cpu().numpy()
    return e / np.linalg.norm(e)


ENCODERS = {"resemblyzer": _resemblyzer_embed, "ecapa": _ecapa_embed}


def prepare_encoder(encoder: str | None, progress: ProgressFn = null_progress) -> tuple[str, str]:
    """Download/load the speaker model. Returns (encoder actually used, fallback note)."""
    encoder = encoder or DEFAULT_ENCODER
    if encoder == "ecapa":
        try:
            _load_ecapa(progress)
            return "ecapa", ""
        except Exception as e:  # e.g. speechbrain missing, or offline before the one-time download
            log.warning("ECAPA unavailable, falling back to resemblyzer: %s: %s", type(e).__name__, e)
            note = f" [ecapa unavailable ({type(e).__name__}: {e}); used resemblyzer]"
            encoder = "resemblyzer"
    else:
        note = ""
    progress(None, "Loading speaker model (resemblyzer)…")
    _resemblyzer_embed(np.zeros(SAMPLE_RATE, dtype=np.float32))  # loads the model
    return encoder, note


def embed_spans(audio: np.ndarray, spans: list[tuple[float, float]], encoder: str = "resemblyzer",
                progress: ProgressFn = null_progress) -> list[np.ndarray | None]:
    embed = ENCODERS[encoder]
    out: list[np.ndarray | None] = []
    for i, (start, end) in enumerate(spans):
        chunk = audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
        out.append(embed(chunk) if len(chunk) >= MIN_EMBED_SECONDS * SAMPLE_RATE else None)
        progress((i + 1) / len(spans), f"Voice prints ({encoder}): sentence {i + 1} of {len(spans)}")
    return out


def _cluster(X: np.ndarray, k: int, linkage: str) -> np.ndarray:
    return AgglomerativeClustering(n_clusters=k, metric="cosine", linkage=linkage).fit_predict(X)


def _centroids(X: np.ndarray, labels: np.ndarray) -> np.ndarray:
    c = np.stack([X[labels == k].mean(axis=0) for k in sorted(set(labels))])
    return c / np.linalg.norm(c, axis=1, keepdims=True)


def _relabel(labels: np.ndarray) -> np.ndarray:
    """Make labels contiguous 0..K-1."""
    _, inv = np.unique(labels, return_inverse=True)
    return inv


def choose_k(X: np.ndarray, max_speakers: int, p: DiarizeParams) -> tuple[int, np.ndarray, str]:
    """Pick the speaker count by silhouette score, guarded against over-splitting one voice."""
    n = len(X)
    best = (1, np.zeros(n, dtype=int), -1.0)
    for k in range(2, min(max_speakers, n - 1) + 1):
        labels = _cluster(X, k, p.linkage)
        if min(np.bincount(labels)) < 2:  # a singleton cluster is an outlier, not a speaker
            continue
        score = silhouette_score(X, labels, metric="cosine")
        if score > best[2]:
            best = (k, labels, score)
    k, labels, score = best
    if k == 1:
        return 1, labels, "single speaker (no valid split)"
    C = _centroids(X, labels)
    max_sim = float((C @ C.T)[~np.eye(k, dtype=bool)].max())
    detail = f"best K={k} silhouette={score:.3f} max centroid sim={max_sim:.3f}"
    if score < p.min_silhouette or max_sim > p.max_centroid_similarity:
        return 1, np.zeros(n, dtype=int), detail + " -> rejected, 1 speaker"
    return k, labels, detail


def _fold_small_clusters(X: np.ndarray, labels: np.ndarray, weights: np.ndarray,
                         min_share: float) -> tuple[np.ndarray, int]:
    """Merge clusters holding < min_share of total weight into their nearest cluster."""
    folded = 0
    while min_share > 0 and len(set(labels)) > 1:
        share = np.bincount(labels, weights=weights) / weights.sum()
        small = int(np.argmin(share))
        if share[small] >= min_share:
            break
        C = _centroids(X, labels)
        sims = C @ C[small]
        sims[small] = -np.inf
        labels = _relabel(np.where(labels == small, int(np.argmax(sims)), labels))
        folded += 1
    return labels, folded


def assign_speakers(embs: list[np.ndarray | None], spans: list[tuple[float, float]],
                    num_speakers: int | None = None, max_speakers: int = 4,
                    params: DiarizeParams = DEFAULT_PARAMS) -> DiarizationResult:
    """Cluster precomputed span embeddings into speakers."""
    p = params
    reliable = [i for i, (s, e) in enumerate(spans) if embs[i] is not None and e - s >= p.reliable_seconds]
    if len(reliable) < 3:
        reliable = [i for i, e in enumerate(embs) if e is not None]
    if len(reliable) < 2:
        return DiarizationResult([1] * len(spans), 1, "too little speech to separate speakers")

    X = np.stack([embs[i] for i in reliable])
    weights = np.array([spans[i][1] - spans[i][0] for i in reliable])
    if num_speakers:
        k = max(1, min(num_speakers, len(reliable)))
        labels = _cluster(X, k, p.linkage) if k > 1 else np.zeros(len(X), dtype=int)
        detail = f"fixed K={k}"
    else:
        k, labels, detail = choose_k(X, max_speakers, p)
        labels, folded = _fold_small_clusters(X, labels, weights, p.min_cluster_share)
        if folded:
            detail += f", folded {folded} small cluster(s) -> K={len(set(labels))}"

    C = _centroids(X, labels)
    raw = np.full(len(spans), -1)
    raw[reliable] = labels
    # Short spans with an embedding: nearest centroid.
    for i, e in enumerate(embs):
        if raw[i] < 0 and e is not None:
            raw[i] = int(np.argmax(C @ e))
    # Spans too short to embed: take the label of the nearest labelled span in time.
    labelled = np.flatnonzero(raw >= 0)
    mids = np.array([(s + e) / 2 for s, e in spans])
    for i in np.flatnonzero(raw < 0):
        raw[i] = raw[labelled[np.argmin(np.abs(mids[labelled] - mids[i]))]]

    # Number speakers 1..K in order of first appearance.
    order: dict[int, int] = {}
    for r in raw:
        order.setdefault(int(r), len(order) + 1)
    return DiarizationResult([order[int(r)] for r in raw], len(order), detail)


def diarize(audio: np.ndarray, spans: list[tuple[float, float]], num_speakers: int | None = None,
            max_speakers: int = 4, encoder: str | None = None,
            params: DiarizeParams | None = None, progress: ProgressFn = null_progress,
            fallback_note: str = "") -> DiarizationResult:
    """Diarize with `encoder` (default ECAPA, falling back to resemblyzer if it can't load).

    Call prepare_encoder() first to load the model as its own progress step;
    otherwise it is loaded here.
    """
    if not spans:
        return DiarizationResult([], 0, "no speech")
    if encoder is None or encoder not in _encoders:
        encoder, fallback_note = prepare_encoder(encoder, progress)
    try:
        embs = embed_spans(audio, spans, encoder, progress)
    except Exception as e:
        if encoder == "resemblyzer":
            raise
        log.warning("%s failed while embedding, falling back to resemblyzer: %s", encoder, e)
        fallback_note = f" [{encoder} failed ({type(e).__name__}: {e}); used resemblyzer]"
        encoder = "resemblyzer"
        embs = embed_spans(audio, spans, encoder, progress)
    result = assign_speakers(embs, spans, num_speakers, max_speakers, params or PARAMS_BY_ENCODER[encoder])
    result.detail = f"{encoder}: {result.detail}{fallback_note}"
    log.info("Diarization: %d speaker(s) — %s", result.num_speakers, result.detail)
    return result
