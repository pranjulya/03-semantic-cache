"""M5: score a verifier on the labelled pairs (Set A) and compare verifiers.

Pairs come from `sweep.pair_sims` + `sweep.with_guard` (fields `sim`, `guard_ok`). A verifier is only ever asked about
pairs the pipeline would send it: similarity >= LOW and entity guard agrees.
"""
import time
from collections.abc import Sequence

import numpy as np

from scache.verifier import Verifier


def score_pairs(pairs: list[dict], verifier: Verifier) -> list[dict]:
    """Copies of `pairs` with the verifier's verdict (`v_same`), score (`v_score`) and time (`v_ms`)."""
    out = []
    for p in pairs:
        t0 = time.perf_counter()
        same, score = verifier.same_intent(p["q1"], p["q2"])
        out.append({**p, "v_same": bool(same), "v_score": float(score), "v_ms": (time.perf_counter() - t0) * 1000})
    return out


def reachable(pairs: list[dict], low: float) -> list[dict]:
    """Pairs that get past the similarity floor and the entity guard, i.e. the ones a verifier would see."""
    return [p for p in pairs if p["sim"] >= low and p.get("guard_ok", True)]


def _summary(pairs: list[dict]) -> dict:
    para = [p for p in pairs if p["label"] == "hit"]
    miss = [p for p in pairs if p["label"] == "miss"]
    ms = [p["v_ms"] for p in pairs]
    return {
        "n_paraphrase": len(para),
        "paraphrase_accepted": sum(p["v_same"] for p in para),
        "n_miss": len(miss),
        "miss_accepted": sum(p["v_same"] for p in miss),
        "near_miss_accepted": sum(p["v_same"] for p in miss if p["type"] == "near_miss"),
        "n_near_miss": sum(p["type"] == "near_miss" for p in miss),
        "accuracy": (sum(p["v_same"] for p in para) + sum(not p["v_same"] for p in miss)) / len(pairs) if pairs else 0.0,
        "ms_p50": float(np.percentile(ms, 50)) if ms else 0.0,
        "ms_p95": float(np.percentile(ms, 95)) if ms else 0.0,
    }


def verifier_report(scored: list[dict], low: float, high: float) -> dict:
    """Verifier outcome on three views of the pairs: all, reachable (sim >= LOW, guard ok), gray zone and above-HIGH."""
    reach = reachable(scored, low)
    return {
        "all": _summary(scored),
        "reachable": _summary(reach),
        "gray": _summary([p for p in reach if p["sim"] < high]),
        "above_high": _summary([p for p in reach if p["sim"] >= high]),
    }


def choose_score_threshold(scored: list[dict], low: float, candidates: Sequence[float] | None = None) -> float:
    """Score threshold for a scoring verifier: the most reachable paraphrases accepted subject to zero reachable
    misses accepted (calibration split only). On a plateau take the middle one."""
    reach = reachable(scored, low)
    grid = sorted(candidates if candidates is not None else {round(p["v_score"], 6) for p in reach} | {1.0})
    para = [p["v_score"] for p in reach if p["label"] == "hit"]
    miss = [p["v_score"] for p in reach if p["label"] == "miss"]
    ok = [t for t in grid if not any(s >= t for s in miss)]
    if not ok:
        raise ValueError("no threshold rejects every miss")
    best = max(sum(s >= t for s in para) for t in ok)
    plateau = [t for t in ok if sum(s >= t for s in para) == best]
    return plateau[len(plateau) // 2]


def apply_threshold(scored: list[dict], threshold: float) -> list[dict]:
    return [{**p, "v_same": p["v_score"] >= threshold} for p in scored]


def low_table(scored: list[dict], high: float, lows: Sequence[float]) -> list[dict]:
    """For each candidate LOW: what the pipeline would do with these pairs when the verifier decides the gray zone.
    `scored` needs `v_same` for every pair (also those below the current LOW)."""
    rows = []
    for low in lows:
        reach = reachable(scored, low)
        top = [p for p in reach if p["sim"] >= high]
        gray = [p for p in reach if p["sim"] < high]
        accepted = [p for p in gray if p["v_same"]]
        rows.append({
            "low": low,
            "hits_direct": sum(p["label"] == "hit" for p in top),
            "false_direct": sum(p["label"] == "miss" for p in top),
            "hits_verified": sum(p["label"] == "hit" for p in accepted),
            "false_verified": sum(p["label"] == "miss" for p in accepted),
            "verifier_calls": len(gray),
            "n_paraphrase": sum(p["label"] == "hit" for p in scored),
        })
    return rows


def choose_low_verified(rows: list[dict]) -> float:
    """Calibration rule: the LOW with the most verified hits subject to zero verified false accepts; among ties the
    highest LOW (fewest verifier calls)."""
    ok = [r for r in rows if r["false_verified"] == 0]
    if not ok:
        raise ValueError("every LOW lets the verifier accept a wrong pair")
    best = max(r["hits_verified"] for r in ok)
    return max(r["low"] for r in ok if r["hits_verified"] == best)
