"""M3 calibration: sweep (LOW, HIGH) over labelled pairs (Set A) and choose thresholds on the calibration split.

A pair is (q_new, q_cached) with label "hit" (paraphrase) or "miss" (different intent / near-miss).
Rule for a pair with cosine similarity s, per (LOW, HIGH):
  s >= HIGH                 -> candidate hit (counted as a hit if the entity guard, when given, agrees)
  LOW <= s < HIGH           -> gray zone: sent to the verifier (not built yet; reported separately)
  s < LOW                   -> miss
"""
import math
from collections.abc import Sequence
from typing import Protocol

import numpy as np
from pydantic import BaseModel


class _Embedder(Protocol):
    def encode(self, texts: list[str], batch_size: int = 32): ...


class SweepRow(BaseModel):
    low: float
    high: float
    n_paraphrase: int
    n_miss: int
    hits: int  # paraphrases with s >= HIGH
    false_hits: int  # miss-label pairs with s >= HIGH
    near_miss_false_hits: int  # ... of type near_miss
    gray_paraphrase: int  # paraphrases in the gray zone
    gray_miss: int  # miss-label pairs in the gray zone

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n_paraphrase if self.n_paraphrase else 0.0

    @property
    def false_hit_rate(self) -> float:
        return self.false_hits / self.n_miss if self.n_miss else 0.0

    @property
    def oracle_hit_rate(self) -> float:
        """Hit rate if a perfect verifier accepted every gray-zone paraphrase and rejected every other gray pair."""
        return (self.hits + self.gray_paraphrase) / self.n_paraphrase if self.n_paraphrase else 0.0


def pair_sims(pairs: list[dict], embedder: _Embedder) -> list[dict]:
    """Return copies of `pairs` with a `sim` field (cosine of the two questions; vectors are normalised)."""
    a = embedder.encode([p["q1"] for p in pairs])
    b = embedder.encode([p["q2"] for p in pairs])
    return [{**p, "sim": float(np.dot(x, y))} for p, x, y in zip(pairs, a, b)]


def with_guard(pairs: list[dict], compatible) -> list[dict]:
    """Copies of `pairs` with `guard_ok` = compatible(q1, q2), the entity guard's verdict."""
    return [{**p, "guard_ok": bool(compatible(p["q1"], p["q2"]))} for p in pairs]


def guard_report(pairs: list[dict]) -> dict:
    """How the guard treats each pair type: rejected / total, plus the ids of paraphrases it wrongly rejects."""
    by_type: dict[str, dict] = {}
    for p in pairs:
        t = by_type.setdefault(p["type"], {"total": 0, "rejected": 0})
        t["total"] += 1
        t["rejected"] += not p.get("guard_ok", True)
    return {
        "by_type": by_type,
        "paraphrases_rejected": [p["id"] for p in pairs if p["label"] == "hit" and not p.get("guard_ok", True)],
    }


def sweep(pairs: list[dict], highs: Sequence[float], lows: Sequence[float]) -> list[SweepRow]:
    """One row per (low, high) with low < high. Pairs need `sim`, `label`, `type`; `guard_ok` (default True) is
    the entity-guard verdict (M4): a pair the guard rejects is never a hit."""
    paraphrase = [p for p in pairs if p["label"] == "hit"]
    miss = [p for p in pairs if p["label"] == "miss"]
    rows = []
    for high in highs:
        for low in lows:
            if not low < high:
                continue
            top = lambda p: p["sim"] >= high and p.get("guard_ok", True)  # noqa: E731
            gray = lambda p: low <= p["sim"] < high  # noqa: E731
            rows.append(SweepRow(
                low=low, high=high, n_paraphrase=len(paraphrase), n_miss=len(miss),
                hits=sum(top(p) for p in paraphrase),
                false_hits=sum(top(p) for p in miss),
                near_miss_false_hits=sum(top(p) for p in miss if p["type"] == "near_miss"),
                gray_paraphrase=sum(gray(p) and p.get("guard_ok", True) for p in paraphrase),
                gray_miss=sum(gray(p) for p in miss),
            ))
    return rows


def frange(start: float, stop: float, step: float) -> list[float]:
    """Inclusive float range without accumulating error."""
    n = int(round((stop - start) / step))
    return [round(start + i * step, 6) for i in range(n + 1)]


def choose_high(rows: list[SweepRow], max_false_rate: float = 0.01) -> SweepRow:
    """Calibration rule (LLD): most hits subject to zero near-miss false hits and false-hit rate <= max_false_rate.
    Ties are broken by the median HIGH of the tied rows (the middle of the plateau, as in project 02's gate)."""
    ok = [r for r in rows if r.near_miss_false_hits == 0 and r.false_hit_rate <= max_false_rate]
    if not ok:
        raise ValueError("no threshold satisfies the constraints")
    best = max(r.hits for r in ok)
    plateau = sorted((r for r in ok if r.hits == best), key=lambda r: r.high)
    return plateau[len(plateau) // 2]


def choose_low(pairs: list[dict], high: float, quantile: float = 0.05, step: float = 0.005) -> float:
    """PROVISIONAL LOW (until the M5 verifier is measured): the `quantile` of paraphrase similarities, rounded down
    to the grid, so that about 1 - quantile of true paraphrases reach the verifier or a direct hit."""
    sims = [p["sim"] for p in pairs if p["label"] == "hit" and p.get("guard_ok", True)]
    low = math.floor(float(np.quantile(sims, quantile)) / step) * step
    return round(min(low, high - step), 6)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def run_sweep(pairs: list[dict], max_false_rate: float = 0.01, low_quantile: float = 0.05,
              highs: Sequence[float] | None = None) -> dict:
    """Calibrate on the calibration split, then score the held-out split once with the chosen values."""
    highs = list(highs) if highs is not None else frange(0.50, 1.00, 0.005)
    cal = [p for p in pairs if p["split"] == "calibration"]
    held = [p for p in pairs if p["split"] == "heldout"]
    grid = sweep(cal, highs, [0.0])  # LOW does not affect hits/false hits, only the gray counts
    best = choose_high(grid, max_false_rate)
    low = choose_low(cal, best.high, low_quantile)
    chosen_cal = sweep(cal, [best.high], [low])[0]
    chosen_held = sweep(held, [best.high], [low])[0]
    table_highs = sorted({h for h in highs if abs(h * 100 - round(h * 100)) < 1e-6 and round(h * 100) % 2 == 0 and h >= 0.70} | {best.high})
    return {
        "rule": {"max_false_rate": max_false_rate, "near_miss_false_hits": 0, "low_quantile": low_quantile},
        "chosen": {"low": low, "high": best.high},
        "calibration": chosen_cal.model_dump(),
        "heldout": chosen_held.model_dump(),
        "heldout_false_hit_ci95": wilson(chosen_held.false_hits, chosen_held.n_miss),
        "heldout_hit_rate_ci95": wilson(chosen_held.hits, chosen_held.n_paraphrase),
        "table": {
            split: [r.model_dump() for r in sweep(rows, table_highs, [low])]
            for split, rows in (("calibration", cal), ("heldout", held))
        },
    }


def render_table(rows: list[dict], chosen_high: float | None = None) -> str:
    out = ["  HIGH   hits  false  near-miss  gray(para/miss)  oracle-hit-rate"]
    for r in rows:
        row = SweepRow(**r)
        mark = " <- chosen" if chosen_high is not None and abs(row.high - chosen_high) < 1e-9 else ""
        out.append(f"  {row.high:.3f}  {row.hits:>2}/{row.n_paraphrase:<3} {row.false_hits:>2}/{row.n_miss:<3} "
                   f"{row.near_miss_false_hits:>5}      {row.gray_paraphrase:>3}/{row.gray_miss:<3}         "
                   f"{row.oracle_hit_rate:.2f}{mark}")
    return "\n".join(out)
