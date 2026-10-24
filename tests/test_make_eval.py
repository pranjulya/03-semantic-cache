import json
import re
from collections import Counter

from eval_data import CONTROLS, FACTS, NEAR_KINDS
from make_eval import ROOT, all_intents, build_pairs, build_security, build_staleness, build_traffic, render_policy

PERSONAL = re.compile(r"\bmy (order|account)\b", re.I)


def test_policy_versions_differ_in_exactly_ten_fact_lines():
    v1, v2 = render_policy("v1").splitlines(), render_policy("v2").splitlines()
    assert len(v1) == len(v2)
    diff = [(a, b) for a, b in zip(v1, v2) if a != b and not a.startswith("Policy version")]
    assert len(diff) == len(FACTS) == 10


def test_changed_values_appear_in_their_policy_version():
    p1, p2 = render_policy("v1"), render_policy("v2")
    for fact, (old, new) in FACTS.items():
        assert old in p1 and new in p2, fact


def test_controls_are_in_both_policies():
    p1, p2 = render_policy("v1"), render_policy("v2")
    for fact, marker, _ in CONTROLS:
        assert marker in p1 and marker in p2, fact


def test_policy_files_match_generator():
    assert (ROOT / "data" / "policy.md").read_text() == render_policy("v1")
    assert (ROOT / "data" / "policy_v2.md").read_text() == render_policy("v2")


def test_pairs_counts_and_stratified_split():
    pairs = build_pairs()
    assert Counter(p["type"] for p in pairs) == {"paraphrase": 50, "different": 50, "near_miss": 50}
    for typ in ("paraphrase", "different", "near_miss"):
        assert Counter(p["split"] for p in pairs if p["type"] == typ) == {"calibration": 35, "heldout": 15}
    assert {p["label"] for p in pairs if p["type"] == "paraphrase"} == {"hit"}
    assert {p["label"] for p in pairs if p["type"] != "paraphrase"} == {"miss"}
    assert len({p["id"] for p in pairs}) == 150


def test_pairs_are_deterministic_and_well_formed():
    assert build_pairs() == build_pairs()
    for p in build_pairs():
        assert p["q1"] != p["q2"]
        assert not PERSONAL.search(p["q1"] + " " + p["q2"])
        if p["type"] == "near_miss":
            assert p["kind"] in NEAR_KINDS


def test_no_question_text_shared_between_calibration_and_heldout():
    pairs = build_pairs()
    texts = {s: {q for p in pairs if p["split"] == s for q in (p["q1"], p["q2"])} for s in ("calibration", "heldout")}
    shared = texts["calibration"] & texts["heldout"]
    # Near-miss pairs reuse a template with other values; identical full sentences should be rare.
    assert len(shared) <= 5, shared


def test_intent_pool_is_big_and_wordings_unique():
    intents = all_intents()
    assert len(intents) >= 180
    assert len({i["id"] for i in intents}) == len(intents)
    wordings = [w for i in intents for w in i["wordings"]]
    assert all(len(i["wordings"]) == 3 for i in intents)
    assert len(set(wordings)) == len(wordings)
    assert not [w for w in wordings if PERSONAL.search(w)]


def test_traffic_repeat_rates_and_flags():
    for pct in (10, 30, 60):
        rows = build_traffic(pct)
        assert len(rows) == 200
        assert sum(r["repeat"] for r in rows) == 200 * pct // 100
        seen = set()
        for r in rows:
            assert r["repeat"] == (r["intent"] in seen)  # flag matches arrival order
            seen.add(r["intent"])
        assert not rows[0]["repeat"]
        assert len({r["id"] for r in rows}) == 200


def test_traffic_repeats_mix_verbatim_and_reworded():
    rows = build_traffic(60)
    first = {}
    verbatim = reworded = 0
    for r in rows:
        if r["repeat"]:
            if r["question"] in first[r["intent"]]:
                verbatim += 1
            else:
                reworded += 1
        first.setdefault(r["intent"], set()).add(r["question"])
    assert verbatim > 10 and reworded > 10


def test_traffic_files_match_generator():
    for pct in (10, 30, 60):
        path = ROOT / "eval" / f"traffic_{pct}.jsonl"
        assert [json.loads(l) for l in path.read_text().splitlines()] == build_traffic(pct)


def test_staleness_set():
    rows = build_staleness()
    assert len(rows) == 30
    assert sum(r["changed"] for r in rows) == 20
    assert all(r["v1"] != r["v2"] for r in rows if r["changed"])
    assert all(r["v1"] == r["v2"] for r in rows if not r["changed"])
    assert len({r["id"] for r in rows}) == 30


def test_security_set():
    rows = build_security()
    assert Counter(r["kind"] for r in rows) == {"personal": 5, "poison": 5}
    assert {r["expect"] for r in rows if r["kind"] == "personal"} == {"bypass"}


def test_answer_checks_shape():
    from make_eval import build_answers

    rows = build_answers()
    assert len(rows) == 47 and len({r["id"] for r in rows}) == 47
    assert Counter(r["answerable"] for r in rows) == {True: 35, False: 12}
    for r in rows:
        assert r["split"] in ("dev", "test")
        if r["answerable"]:
            assert r["expect"] and all(group for group in r["expect"])
            for alt in (a for g in r["expect"] for a in g):
                if alt.startswith("re:"):
                    re.compile(alt[3:])
                assert alt.lower() != "no"  # a bare "no" matches inside "not"
        else:
            assert r["expect"] == []
    for answerable in (True, False):
        splits = Counter(r["split"] for r in rows if r["answerable"] == answerable)
        assert splits["dev"] > splits["test"] > 0


def test_answer_checks_file_matches_generator():
    from make_eval import build_answers

    path = ROOT / "eval" / "answers.jsonl"
    assert [json.loads(l) for l in path.read_text().splitlines()] == build_answers()


def test_intent_aliases_name_real_intents():
    from scache.replay import INTENT_ALIASES

    ids = {i["id"] for i in all_intents()}
    assert all(k in ids and v in ids for k, v in INTENT_ALIASES.items())
