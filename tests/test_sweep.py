import json

import pytest

from fakes import FakeEmbedder, unit_at
from scache.sweep import (choose_high, choose_low, frange, pair_sims, render_table, run_sweep, sweep, wilson)


def pair(sim, label, typ=None, split="calibration", **kw):
    typ = typ or ("paraphrase" if label == "hit" else "different")
    return {"sim": sim, "label": label, "type": typ, "split": split, **kw}


PAIRS = [
    pair(0.95, "hit"), pair(0.90, "hit"), pair(0.80, "hit"), pair(0.60, "hit"),
    pair(0.92, "miss", "different"), pair(0.70, "miss", "different"),
    pair(0.93, "miss", "near_miss"), pair(0.50, "miss", "near_miss"),
]


def test_sweep_counts_hits_false_hits_and_gray():
    (r,) = sweep(PAIRS, [0.9], [0.7])
    assert (r.n_paraphrase, r.n_miss) == (4, 4)
    assert r.hits == 2  # 0.95, 0.90 (>= is inclusive)
    assert r.false_hits == 2 and r.near_miss_false_hits == 1  # 0.92 and 0.93
    assert r.gray_paraphrase == 1  # 0.80
    assert r.gray_miss == 1  # 0.70
    assert r.hit_rate == 0.5 and r.false_hit_rate == 0.5 and r.oracle_hit_rate == 0.75


def test_sweep_skips_low_not_below_high():
    rows = sweep(PAIRS, [0.9], [0.5, 0.9, 0.95])
    assert [r.low for r in rows] == [0.5]


def test_guard_rejection_removes_hit_and_false_hit_but_not_gray_miss():
    pairs = [pair(0.95, "hit", guard_ok=False), pair(0.95, "miss", "near_miss", guard_ok=False),
             pair(0.80, "hit"), pair(0.80, "miss")]
    (r,) = sweep(pairs, [0.9], [0.7])
    assert r.hits == 0 and r.false_hits == 0 and r.near_miss_false_hits == 0
    assert r.gray_paraphrase == 1 and r.gray_miss == 1


def test_empty_classes_do_not_divide_by_zero():
    (r,) = sweep([], [0.9], [0.5])
    assert r.hit_rate == 0 and r.false_hit_rate == 0 and r.oracle_hit_rate == 0


def test_frange_inclusive_and_exact():
    assert frange(0.5, 0.6, 0.05) == [0.5, 0.55, 0.6]
    assert frange(0.5, 1.0, 0.005)[-1] == 1.0 and len(frange(0.5, 1.0, 0.005)) == 101


def test_choose_high_obeys_constraints_and_maximises_hits():
    rows = sweep(PAIRS, frange(0.5, 1.0, 0.01), [0.0])
    best = choose_high(rows, max_false_rate=0.01)
    assert best.near_miss_false_hits == 0 and best.false_hit_rate <= 0.01
    assert best.hits == 1  # only the 0.95 paraphrase clears every miss pair (max miss sim 0.93)
    assert 0.93 < best.high <= 0.95


def test_choose_high_picks_plateau_median():
    pairs = [pair(0.95, "hit"), pair(0.80, "miss", "different")]
    rows = sweep(pairs, [0.82, 0.84, 0.86, 0.88, 0.90], [0.0])
    assert choose_high(rows).high == 0.86  # all five tie with 1 hit and 0 false hits


def test_choose_high_loosened_false_rate_allows_more_hits():
    pairs = [pair(0.95, "hit"), pair(0.85, "hit")] + [pair(0.2 + i / 1000, "miss", "different") for i in range(99)] + [pair(0.86, "miss", "different")]
    rows = sweep(pairs, frange(0.5, 1.0, 0.01), [0.0])
    assert choose_high(rows, 0.0).hits == 1
    assert choose_high(rows, 0.01).hits == 2  # one false hit of 100 miss pairs is allowed


def test_choose_high_raises_when_nothing_qualifies():
    with pytest.raises(ValueError):
        choose_high([], 0.01)


def test_choose_low_quantile_rounds_down_and_stays_below_high():
    pairs = [pair(s, "hit") for s in (0.60, 0.70, 0.80, 0.90, 1.0)]
    assert choose_low(pairs, high=0.95, quantile=0.0) == 0.6
    assert choose_low(pairs, high=0.65, quantile=0.5) == 0.645  # 0.80 -> capped just under HIGH
    assert choose_low([pair(0.73, "hit"), pair(0.74, "hit")], high=0.99, quantile=0.0) == 0.73


def test_wilson_interval():
    lo, hi = wilson(0, 15)
    assert lo == 0.0 and 0.18 < hi < 0.22
    lo, hi = wilson(15, 15)
    assert hi == 1.0 and 0.78 < lo < 0.82
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(5, 10)
    assert lo < 0.5 < hi


def test_pair_sims_uses_cosine_of_embeddings():
    emb = FakeEmbedder({"a": unit_at(0), "b": unit_at(60), "c": unit_at(0)})
    out = pair_sims([{"q1": "a", "q2": "b", "id": 1}, {"q1": "a", "q2": "c", "id": 2}], emb)
    assert out[0]["sim"] == pytest.approx(0.5, abs=1e-6) and out[0]["id"] == 1
    assert out[1]["sim"] == pytest.approx(1.0, abs=1e-6)


def test_run_sweep_calibrates_on_calibration_only():
    cal = [pair(0.97, "hit"), pair(0.96, "hit"), pair(0.85, "hit"), pair(0.90, "miss", "different"),
           pair(0.88, "miss", "near_miss")]
    held = [pair(0.99, "hit", split="heldout"), pair(0.95, "miss", "near_miss", split="heldout")]
    res = run_sweep(cal + held)
    high = res["chosen"]["high"]
    assert 0.90 < high <= 0.96
    assert res["calibration"]["hits"] == 2 and res["calibration"]["false_hits"] == 0
    assert res["heldout"]["hits"] == 1
    assert res["heldout"]["near_miss_false_hits"] == (1 if 0.95 >= high else 0)
    assert res["chosen"]["low"] < high
    lo, hi = res["heldout_false_hit_ci95"]
    assert 0 <= lo <= hi <= 1
    json.dumps(res)  # serialisable


def test_run_sweep_does_not_use_heldout_to_choose():
    cal = [pair(0.97, "hit"), pair(0.80, "miss", "different")]
    a = run_sweep(cal + [pair(0.99, "miss", "near_miss", split="heldout")])
    b = run_sweep(cal + [pair(0.10, "miss", "near_miss", split="heldout")])
    assert a["chosen"] == b["chosen"]


def test_render_table_marks_chosen():
    rows = [r.model_dump() for r in sweep(PAIRS, [0.9, 0.95], [0.7])]
    text = render_table(rows, 0.95)
    assert "HIGH" in text and text.count("<- chosen") == 1 and "0.950" in text


from scache.sweep import guard_report, with_guard  # noqa: E402


def test_with_guard_adds_verdict_without_mutating():
    pairs = [{"q1": "a 1", "q2": "a 2", "label": "miss", "type": "near_miss", "sim": 0.9}]
    out = with_guard(pairs, lambda a, b: a == b)
    assert out[0]["guard_ok"] is False and "guard_ok" not in pairs[0]


def test_guard_report_counts_by_type_and_lists_wrong_paraphrase_rejections():
    pairs = [
        {"id": "p1", "type": "paraphrase", "label": "hit", "guard_ok": True},
        {"id": "p2", "type": "paraphrase", "label": "hit", "guard_ok": False},
        {"id": "n1", "type": "near_miss", "label": "miss", "guard_ok": False},
        {"id": "n2", "type": "near_miss", "label": "miss", "guard_ok": False},
    ]
    r = guard_report(pairs)
    assert r["by_type"] == {"paraphrase": {"total": 2, "rejected": 1}, "near_miss": {"total": 2, "rejected": 2}}
    assert r["paraphrases_rejected"] == ["p2"]


def test_guard_lets_the_sweep_choose_a_lower_high():
    pairs = [pair(0.95, "hit"), pair(0.90, "hit"), pair(0.93, "miss", "near_miss", guard_ok=False),
             pair(0.85, "miss", "different")]
    no_guard = run_sweep([{k: v for k, v in p.items() if k != "guard_ok"} for p in pairs])
    guarded = run_sweep(pairs)
    assert no_guard["calibration"]["hits"] == 1 and guarded["calibration"]["hits"] == 2
    assert guarded["chosen"]["high"] < no_guard["chosen"]["high"]
