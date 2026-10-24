import json

import pytest

from scache.llm import LLMError
from scache.models import Record
from scache.replay import load_traffic, replay, summarize


def rec(q, ms=10.0, llm=8.0, decision="miss"):
    return Record(question=q, answer="a", decision=decision, similarity=None, matched_question=None,
                  latency_ms=ms, llm_ms=llm, model_tag="m")


def test_load_traffic(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"id": "1", "question": "a", "intent": "i"}) + "\n\n")
    assert load_traffic(p) == [{"id": "1", "question": "a", "intent": "i"}]


def test_replay_passes_intent_in_order():
    calls = []

    def ask(q, intent=None):
        calls.append((q, intent))
        return rec(q)

    out = replay([{"question": "a", "intent": "x"}, {"question": "b"}], ask)
    assert calls == [("a", "x"), ("b", None)]
    assert [r.question for r in out] == ["a", "b"]


def test_replay_skips_llm_errors_and_reports_them():
    errors = []

    def ask(q, intent=None):
        if q == "bad":
            raise LLMError("HTTP 500")
        return rec(q)

    items = [{"id": "1", "question": "ok"}, {"id": "2", "question": "bad"}, {"id": "3", "question": "ok2"}]
    out = replay(items, ask, on_error=lambda item, e: errors.append(item["id"]))
    assert [r.question for r in out] == ["ok", "ok2"]
    assert errors == ["2"]


def test_replay_does_not_swallow_other_errors():
    def ask(q, intent=None):
        raise ValueError("bug")

    with pytest.raises(ValueError):
        replay([{"question": "a"}], ask)


def test_summarize():
    s = summarize([rec("a", 100, 80), rec("b", 200, 150), rec("c", 300, 250, "hit")])
    assert s["n"] == 3
    assert s["decisions"] == {"miss": 2, "hit": 1}
    assert s["latency_ms"]["p50"] == 200 and s["latency_ms"]["mean"] == 200
    assert s["llm_total_s"] == 0.48
    assert s["wall_total_s"] == 0.6


def test_summarize_empty():
    s = summarize([])
    assert s["n"] == 0 and s["latency_ms"]["p50"] is None and s["llm_total_s"] == 0


from scache.replay import hit_quality, intent_map, replay_items  # noqa: E402


def hit(q, matched, sim=0.9):
    return Record(question=q, answer="a", decision="hit", similarity=sim, matched_question=matched,
                  latency_ms=5, llm_ms=None, model_tag="m")


def test_intent_map_and_conflict():
    assert intent_map([{"question": "a", "intent": "x"}, {"question": "a", "intent": "x"}]) == {"a": "x"}
    with pytest.raises(ValueError):
        intent_map([{"question": "a", "intent": "x"}, {"question": "a", "intent": "y"}])


def test_replay_items_pairs_items_with_records():
    items = [{"id": "1", "question": "a"}, {"id": "2", "question": "b"}]
    out = replay_items(items, lambda q, intent=None: rec(q))
    assert [(i["id"], r.question) for i, r in out] == [("1", "a"), ("2", "b")]


def test_hit_quality_counts_true_and_false_hits():
    intent_of = {"q1": "i1", "q1b": "i1", "q2": "i2"}
    pairs = [
        ({"id": "1", "question": "q1", "intent": "i1", "repeat": False}, rec("q1")),
        ({"id": "2", "question": "q1b", "intent": "i1", "repeat": True}, hit("q1b", "q1")),   # true hit
        ({"id": "3", "question": "q2", "intent": "i2", "repeat": False}, hit("q2", "q1", 0.88)),  # false hit
        ({"id": "4", "question": "q1", "intent": "i1", "repeat": True}, rec("q1")),           # missed repeat
    ]
    q = hit_quality(pairs, intent_of)
    assert q["requests"] == 4 and q["hits"] == 2 and q["false_hits"] == 1
    assert q["hit_rate"] == 0.5 and q["false_hit_rate"] == 0.5 and q["false_hit_share_of_requests"] == 0.25
    assert q["repeats"] == 2 and q["repeat_correct_hits"] == 1 and q["repeat_recall"] == 0.5
    (ex,) = q["false_hit_examples"]
    assert ex["id"] == "3" and ex["asked_intent"] == "i2" and ex["matched_intent"] == "i1" and ex["similarity"] == 0.88


def test_hit_quality_without_hits():
    q = hit_quality([({"id": "1", "question": "a", "intent": "i", "repeat": False}, rec("a"))], {"a": "i"})
    assert q["hits"] == 0 and q["false_hit_rate"] is None and q["repeat_recall"] is None


def test_summarize_latency_by_decision():
    s = summarize([rec("a", 100, 80), rec("b", 300, 250), hit("c", "a")])
    assert s["latency_by_decision"]["miss"]["n"] == 2 and s["latency_by_decision"]["miss"]["p50"] == 200
    assert s["latency_by_decision"]["hit"] == {"n": 1, "p50": 5, "p95": 5}


def test_alias_intents_count_as_correct_hits():
    from scache.replay import canonical

    assert canonical("np:hours_sunday") == canonical("hours_on_day:Sunday") == "hours_on_day:Sunday"
    assert canonical("np:other") == "np:other" and canonical(None) is None
    intent_of = {"a": "np:hours_sunday", "b": "hours_on_day:Sunday"}
    pairs = [({"id": "1", "question": "b", "intent": "hours_on_day:Sunday", "repeat": True}, hit("b", "a"))]
    q = hit_quality(pairs, intent_of)
    assert q["false_hits"] == 0 and q["alias_hits"] == 1 and q["repeat_correct_hits"] == 1
