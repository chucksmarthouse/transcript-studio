"""Score a project file's speaker labels against a samples/multispeaker/*.reference.md.

    .venv/bin/python tools/score_diarization.py project.tsproj.json samples/multispeaker/x.reference.md

Each speech sentence gets the ground-truth speaker whose turn contains its
midpoint; predicted labels are mapped to true speakers by the best one-to-one
assignment (Hungarian). Accuracy is duration-weighted.
"""

import bisect
import re
import sys
from collections import defaultdict

import numpy as np
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from transcript_studio.model import Session  # noqa: E402

TURN_RE = re.compile(r"^\[(\d\d):(\d\d)\] \*\*(.+?):\*\*", re.M)


def load_reference(path):
    turns = [(int(m) * 60 + int(s), name) for m, s, name in TURN_RE.findall(open(path, encoding="utf-8").read())]
    return [t for t, _ in turns], [n for _, n in turns]


def score(session: Session, ref_path: str) -> dict:
    return score_labels([(s.start_time, s.end_time, p.speaker_id) for p, s in session.iter_sentences()
                         if not p.is_nonspeech and p.speaker_id is not None], ref_path)


def score_labels(labelled: list[tuple[float, float, int]], ref_path: str) -> dict:
    """labelled: (start, end, predicted speaker) per speech sentence."""
    starts, names = load_reference(ref_path)
    pairs = defaultdict(float)  # (pred, true) -> seconds
    for start, end, pred in labelled:
        i = bisect.bisect_right(starts, (start + end) / 2) - 1
        if i >= 0:
            pairs[(pred, names[i])] += end - start
    preds = sorted({p for p, _ in pairs})
    trues = sorted({t for _, t in pairs})
    M = np.array([[pairs[(p, t)] for t in trues] for p in preds])
    rows, cols = linear_sum_assignment(-M)
    correct = M[rows, cols].sum()
    return {
        "accuracy": correct / M.sum(),
        "predicted_speakers": len(preds),
        "true_speakers": len(trues),
        "mapping": {f"Participant {preds[r]}": trues[c] for r, c in zip(rows, cols)},
        "confusion": {f"P{p}": {t: round(pairs[(p, t)], 1) for t in trues} for p in preds},
    }


if __name__ == "__main__":
    result = score(Session.load(sys.argv[1]), sys.argv[2])
    print(f"accuracy {result['accuracy']:.1%}  predicted {result['predicted_speakers']} "
          f"vs true {result['true_speakers']} speakers")
    print("mapping:", result["mapping"])
    for p, row in result["confusion"].items():
        print(f"  {p}: {row}")
