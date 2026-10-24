import json
from collections.abc import Callable
from pathlib import Path

import numpy as np

from scache.llm import LLMError
from scache.models import Record


def load_traffic(path: Path | str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def replay_items(traffic: list[dict], ask_fn: Callable[..., Record],
                 on_error: Callable[[dict, Exception], None] | None = None,
                 on_record: Callable[[int, Record], None] | None = None) -> list[tuple[dict, Record]]:
    """Send each question through `ask_fn(question, intent=...)` in order; return (traffic item, record) pairs.

    An LLM failure is reported to `on_error` and skipped, so one HTTP 500 does not abort a long run.
    """
    out = []
    for i, item in enumerate(traffic, 1):
        try:
            rec = ask_fn(item["question"], intent=item.get("intent"))
        except LLMError as e:
            if on_error:
                on_error(item, e)
            continue
        out.append((item, rec))
        if on_record:
            on_record(i, rec)
    return out


def replay(traffic: list[dict], ask_fn: Callable[..., Record],
           on_error: Callable[[dict, Exception], None] | None = None,
           on_record: Callable[[int, Record], None] | None = None) -> list[Record]:
    return [rec for _, rec in replay_items(traffic, ask_fn, on_error, on_record)]


# Intent tags that mean the same thing (a duplicate in the Set B pool, found in M4). A hit between aliases is
# a correct hit. The traffic files are NOT regenerated, so earlier results stay comparable.
INTENT_ALIASES = {"np:hours_sunday": "hours_on_day:Sunday"}


def canonical(intent: str | None) -> str | None:
    return INTENT_ALIASES.get(intent, intent)


def intent_map(traffic: list[dict]) -> dict[str, str]:
    """question text -> intent. Each wording belongs to exactly one intent (a generator test enforces it)."""
    out: dict[str, str] = {}
    for item in traffic:
        if out.setdefault(item["question"], item["intent"]) != item["intent"]:
            raise ValueError(f"question maps to two intents: {item['question']!r}")
    return out


def hit_quality(pairs: list[tuple[dict, Record]], intent_of: dict[str, str], max_examples: int = 25) -> dict:
    """Judge cache hits against the intent tags. A hit is false when the cached question has another intent."""
    hits = [(it, r) for it, r in pairs if r.decision == "hit"]
    same = lambda it, r: canonical(intent_of.get(r.matched_question)) == canonical(it["intent"])  # noqa: E731
    false = [(it, r) for it, r in hits if not same(it, r)]
    alias_hits = [1 for it, r in hits if same(it, r) and intent_of.get(r.matched_question) != it["intent"]]
    repeats = [(it, r) for it, r in pairs if it.get("repeat")]
    good_repeat_hits = [1 for it, r in repeats if r.decision == "hit" and same(it, r)]
    return {
        "requests": len(pairs),
        "hits": len(hits),
        "hit_rate": len(hits) / len(pairs) if pairs else None,
        "false_hits": len(false),
        "alias_hits": len(alias_hits),
        "false_hit_rate": len(false) / len(hits) if hits else None,
        "false_hit_share_of_requests": len(false) / len(pairs) if pairs else None,
        "repeats": len(repeats),
        "repeat_correct_hits": len(good_repeat_hits),
        "repeat_recall": len(good_repeat_hits) / len(repeats) if repeats else None,
        "false_hit_examples": [
            {"id": it.get("id"), "asked": r.question, "asked_intent": it["intent"], "matched": r.matched_question,
             "matched_intent": intent_of.get(r.matched_question), "similarity": r.similarity}
            for it, r in false[:max_examples]
        ],
    }


def _pct(xs: list[float], q: float) -> float | None:
    return float(np.percentile(xs, q)) if xs else None


def summarize(records: list[Record]) -> dict:
    """Latency and compute totals. Compute = seconds the LLM spent answering."""
    lat = [r.latency_ms for r in records]
    llm = [r.llm_ms for r in records if r.llm_ms is not None]
    decisions: dict[str, int] = {}
    for r in records:
        decisions[r.decision] = decisions.get(r.decision, 0) + 1
    by_decision = {}
    for d in decisions:
        xs = [r.latency_ms for r in records if r.decision == d]
        by_decision[d] = {"n": len(xs), "p50": _pct(xs, 50), "p95": _pct(xs, 95)}
    return {
        "n": len(records),
        "decisions": decisions,
        "latency_by_decision": by_decision,
        "latency_ms": {"mean": float(np.mean(lat)) if lat else None, "p50": _pct(lat, 50), "p95": _pct(lat, 95)},
        "llm_ms": {"p50": _pct(llm, 50), "p95": _pct(llm, 95)},
        "llm_total_s": sum(llm) / 1000,
        "wall_total_s": sum(lat) / 1000,
    }
