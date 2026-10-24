"""Exploratory (M2): cosine similarity of Set A pairs on the CALIBRATION split, and the naive F1-best threshold.

Not the M3 sweep: no gray zone, no entity guard, no false-hit constraint. Held-out pairs are never read here.
Usage: uv run python scripts/explore_sims.py
"""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def f1_best_threshold(sims: list[float], labels: list[bool]) -> tuple[float, float]:
    """Threshold t (hit when sim >= t) with the best F1 for the positive class; ties take the highest t."""
    best_t, best_f1 = 1.0, -1.0
    for t in sorted(set(sims)):
        tp = sum(s >= t and y for s, y in zip(sims, labels))
        fp = sum(s >= t and not y for s, y in zip(sims, labels))
        fn = sum(s < t and y for s, y in zip(sims, labels))
        f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
        if f1 >= best_f1:
            best_t, best_f1 = t, f1
    return best_t, best_f1


def summarize(sims: list[float]) -> dict:
    return {"n": len(sims), "min": min(sims), "p25": float(np.percentile(sims, 25)),
            "median": float(np.median(sims)), "p75": float(np.percentile(sims, 75)), "max": max(sims)}


def main() -> None:
    from scache.embed import Embedder

    pairs = [json.loads(l) for l in (ROOT / "eval" / "pairs.jsonl").read_text().splitlines()]
    pairs = [p for p in pairs if p["split"] == "calibration"]
    emb = Embedder()
    a, b = emb.encode([p["q1"] for p in pairs]), emb.encode([p["q2"] for p in pairs])
    sims = [float(x @ y) for x, y in zip(a, b)]
    for typ in ("paraphrase", "different", "near_miss"):
        s = [x for x, p in zip(sims, pairs) if p["type"] == typ]
        print(f"{typ:11}", {k: round(v, 3) for k, v in summarize(s).items()})
    t, f1 = f1_best_threshold(sims, [p["label"] == "hit" for p in pairs])
    fp = [(round(s, 3), p["id"]) for s, p in zip(sims, pairs) if s >= t and p["label"] == "miss"]
    tp = sum(s >= t and p["label"] == "hit" for s, p in zip(sims, pairs))
    print(f"F1-best threshold on calibration: {t:.4f} (F1 {f1:.3f}); hits {tp}/35 paraphrases; false hits {len(fp)}: {fp[:8]}")


if __name__ == "__main__":
    main()
