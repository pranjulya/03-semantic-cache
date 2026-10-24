import pytest

from scache.verifycheck import (
    apply_threshold, choose_score_threshold, reachable, score_pairs, verifier_report,
)


def pair(label, type_, sim, score=0.0, guard_ok=True, split="calibration"):
    return {"id": "x", "q1": "a", "q2": "b", "label": label, "type": type_, "sim": sim, "guard_ok": guard_ok,
            "split": split, "v_score": score, "v_same": False, "v_ms": 1.0}


class Always:
    name = "always"

    def __init__(self, same):
        self.same = same

    def same_intent(self, a, b):
        return self.same, 1.0 if self.same else 0.0


def test_score_pairs_adds_verdict_and_time():
    out = score_pairs([{"q1": "a", "q2": "b"}], Always(True))
    assert out[0]["v_same"] is True and out[0]["v_score"] == 1.0 and out[0]["v_ms"] >= 0


def test_reachable_needs_sim_and_guard():
    ps = [pair("hit", "paraphrase", 0.9), pair("hit", "paraphrase", 0.5), pair("miss", "near_miss", 0.95, guard_ok=False)]
    assert reachable(ps, 0.8) == [ps[0]]


def test_report_views_and_counts():
    ps = [
        {**pair("hit", "paraphrase", 0.85), "v_same": True},
        {**pair("hit", "paraphrase", 0.96), "v_same": False},
        {**pair("miss", "near_miss", 0.9), "v_same": True},
        {**pair("miss", "different", 0.4), "v_same": False},
    ]
    r = verifier_report(ps, low=0.8, high=0.93)
    assert r["all"]["accuracy"] == 0.5
    assert r["reachable"]["n_paraphrase"] == 2 and r["reachable"]["miss_accepted"] == 1
    assert r["gray"]["paraphrase_accepted"] == 1 and r["gray"]["near_miss_accepted"] == 1
    assert r["above_high"]["n_paraphrase"] == 1 and r["above_high"]["paraphrase_accepted"] == 0


def test_choose_threshold_rejects_all_misses_then_accepts_most_paraphrases():
    ps = [pair("hit", "paraphrase", 0.9, s) for s in (0.9, 0.8, 0.3)] + [pair("miss", "near_miss", 0.9, 0.5)]
    t = choose_score_threshold(ps, low=0.8)
    assert 0.5 < t <= 0.8
    assert [p["v_same"] for p in apply_threshold(ps, t)] == [True, True, False, False]


def test_choose_threshold_ignores_unreachable_pairs():
    ps = [pair("hit", "paraphrase", 0.9, 0.9), pair("miss", "different", 0.3, 0.99)]
    assert choose_score_threshold(ps, low=0.8) <= 0.9


def test_choose_threshold_plateau_takes_the_middle():
    ps = [pair("hit", "paraphrase", 0.9, 0.9), pair("miss", "near_miss", 0.9, 0.1)]
    t = choose_score_threshold(ps, low=0.8, candidates=[0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
    assert t == 0.6  # plateau 0.2..0.9 is 8 long, index 4


def test_choose_threshold_errors_when_a_miss_cannot_be_rejected():
    ps = [pair("miss", "near_miss", 0.9, 1.0), pair("hit", "paraphrase", 0.9, 0.9)]
    with pytest.raises(ValueError):
        choose_score_threshold(ps, low=0.8, candidates=[0.5])


def vp(label, sim, same, type_="paraphrase"):
    return {**pair(label, type_ if label == "hit" else "different", sim), "v_same": same}


def test_low_table_counts_verified_hits_and_calls():
    from scache.verifycheck import low_table

    ps = [vp("hit", 0.97, True), vp("hit", 0.85, True), vp("hit", 0.75, True), vp("miss", 0.8, False), vp("miss", 0.7, True)]
    rows = {r["low"]: r for r in low_table(ps, high=0.93, lows=[0.9, 0.8, 0.7])}
    assert rows[0.9]["hits_direct"] == 1 and rows[0.9]["verifier_calls"] == 0
    assert rows[0.8]["hits_verified"] == 1 and rows[0.8]["verifier_calls"] == 2 and rows[0.8]["false_verified"] == 0
    assert rows[0.7]["hits_verified"] == 2 and rows[0.7]["false_verified"] == 1 and rows[0.7]["verifier_calls"] == 5 - 1


def test_choose_low_verified_prefers_most_hits_without_false_accepts_then_highest_low():
    from scache.verifycheck import choose_low_verified

    rows = [
        {"low": 0.7, "hits_verified": 9, "false_verified": 1},
        {"low": 0.75, "hits_verified": 8, "false_verified": 0},
        {"low": 0.8, "hits_verified": 8, "false_verified": 0},
        {"low": 0.85, "hits_verified": 5, "false_verified": 0},
    ]
    assert choose_low_verified(rows) == 0.8
    with pytest.raises(ValueError):
        choose_low_verified([{"low": 0.7, "hits_verified": 1, "false_verified": 1}])
