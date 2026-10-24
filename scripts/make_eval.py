"""Generate policy v1/v2 and the eval sets (A pairs, B traffic, C staleness, security) deterministically.

Usage: uv run python scripts/make_eval.py
"""
import json
import random
from pathlib import Path

from eval_data import (
    ANSWER_CHECKS, CHANGED_QUESTIONS, CONTROLS, DIFFERENT, FACTS, NEAR, NP_INTENTS, PARAM_FAMILIES,
    PARAPHRASE, POLICY_TEMPLATE, PRODUCTS, SECURITY, TOPICS,
)

ROOT = Path(__file__).resolve().parents[1]
SEED = 3
CAL_PER_TYPE, HELD_PER_TYPE = 35, 15
REPEAT_RATES = (10, 30, 60)
VERBATIM_P = 1 / 3  # share of repeats that reuse an earlier wording exactly


def render_policy(version: str) -> str:
    """version 'v1' or 'v2'."""
    idx = {"v1": 0, "v2": 1}[version]
    return POLICY_TEMPLATE.format(version=version, **{k: v[idx] for k, v in FACTS.items()})


def _split(n: int, rng: random.Random) -> list[str]:
    """Split labels for n items: CAL_PER_TYPE calibration, the rest held-out, shuffled."""
    labels = ["calibration"] * CAL_PER_TYPE + ["heldout"] * (n - CAL_PER_TYPE)
    rng.shuffle(labels)
    return labels


def build_pairs(seed: int = SEED) -> list[dict]:
    """Set A: 50 paraphrase (hit), 50 different intent (miss), 50 near-miss (miss). Split 35/15 per type."""
    rng = random.Random(seed)
    groups = [
        ("par", "paraphrase", "hit", [(a, b, None) for a, b in PARAPHRASE]),
        ("dif", "different", "miss", [(a, b, None) for a, b in DIFFERENT]),
        ("near", "near_miss", "miss", [(t.format(a), t.format(b), k) for t, a, b, k in NEAR]),
    ]
    rows = []
    for code, typ, label, items in groups:
        for i, (split, (q1, q2, kind)) in enumerate(zip(_split(len(items), rng), items), 1):
            row = {"id": f"A-{code}-{i:02d}", "type": typ, "q1": q1, "q2": q2, "label": label, "split": split}
            if kind:
                row["kind"] = kind
            rows.append(row)
    return rows


def all_intents() -> list[dict]:
    """Set B intent pool: {'id', 'wordings'}. Parametrised families give one intent per parameter."""
    out = [{"id": f"np:{slug}", "wordings": w} for slug, w in NP_INTENTS]
    for name, wordings, params in PARAM_FAMILIES:
        for p in params or PRODUCTS:
            out.append({"id": f"{name}:{p}", "wordings": [w.format(p) for w in wordings]})
    return out


def build_traffic(repeat_pct: int, n: int = 200, seed: int = SEED) -> list[dict]:
    """Set B: n questions, exactly round(n*pct/100) of them repeat an earlier intent (wording may differ)."""
    rng = random.Random(seed * 1000 + repeat_pct)
    intents = all_intents()
    by_id = {i["id"]: i for i in intents}
    n_rep = round(n * repeat_pct / 100)
    rep_slots = set(rng.sample(range(1, n), n_rep))
    firsts = iter(rng.sample(intents, n - n_rep))
    used: dict[str, list[str]] = {}
    rows = []
    for i in range(n):
        if i in rep_slots:
            iid = rng.choice(sorted(used))
            fresh = [w for w in by_id[iid]["wordings"] if w not in used[iid]]
            q = rng.choice(fresh) if fresh and rng.random() >= VERBATIM_P else rng.choice(used[iid])
            repeat = True
        else:
            it = next(firsts)
            iid, q, repeat = it["id"], rng.choice(it["wordings"]), False
        used.setdefault(iid, []).append(q)
        rows.append({"id": f"t{repeat_pct}-{i + 1:03d}", "question": q, "intent": iid, "repeat": repeat})
    return rows


def build_staleness() -> list[dict]:
    """Set C: 20 questions on the 10 changed facts (2 each) + 10 controls on unchanged facts."""
    items = []
    for fact, qs in CHANGED_QUESTIONS.items():
        v1, v2 = FACTS[fact]
        items += [(q, fact, True, v1, v2) for q in qs]
    items += [(q, fact, False, marker, marker) for fact, marker, q in CONTROLS]
    return [
        {"id": f"C-{i:02d}", "question": q, "fact": fact, "changed": changed, "v1": v1, "v2": v2,
         **({"topic": TOPICS[fact]} if changed else {})}
        for i, (q, fact, changed, v1, v2) in enumerate(items, 1)
    ]


def build_security() -> list[dict]:
    expect = {"personal": "bypass", "poison": "no_poisoned_answer_cached"}
    return [{"id": f"S-{i:02d}", "kind": k, "question": q, "expect": expect[k]} for i, (k, q) in enumerate(SECURITY, 1)]


def build_answers(seed: int = SEED) -> list[dict]:
    """Prompt-tuning checks: ~2/3 dev, ~1/3 test, stratified by answerable/unanswerable."""
    rng = random.Random(seed)
    rows = []
    for answerable in (True, False):
        items = [c for c in ANSWER_CHECKS if c[1] == answerable]
        n_dev = round(len(items) * 2 / 3)
        splits = ["dev"] * n_dev + ["test"] * (len(items) - n_dev)
        rng.shuffle(splits)
        rows += [{"question": q, "answerable": a, "expect": g, "split": sp} for (q, a, g), sp in zip(items, splits)]
    return [{"id": f"P-{i:02d}", **r} for i, r in enumerate(rows, 1)]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def main() -> None:
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "policy.md").write_text(render_policy("v1"))
    (ROOT / "data" / "policy_v2.md").write_text(render_policy("v2"))
    write_jsonl(ROOT / "eval" / "pairs.jsonl", build_pairs())
    for pct in REPEAT_RATES:
        write_jsonl(ROOT / "eval" / f"traffic_{pct}.jsonl", build_traffic(pct))
    write_jsonl(ROOT / "eval" / "staleness.jsonl", build_staleness())
    write_jsonl(ROOT / "eval" / "security.jsonl", build_security())
    write_jsonl(ROOT / "eval" / "answers.jsonl", build_answers())
    print("wrote data/policy.md, data/policy_v2.md, eval/{pairs,traffic_10,traffic_30,traffic_60,staleness,security,answers}.jsonl")


if __name__ == "__main__":
    main()
