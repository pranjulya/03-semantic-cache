import json

from scache.promptcheck import check_answer, evaluate, load_checks

ANS = {"answerable": True, "expect": [["30"], ["days", "day"]]}
YESNO = {"answerable": True, "expect": [["re:^no\\b", "closed"]]}
UNANS = {"answerable": False, "expect": []}


def test_ok_needs_every_group():
    assert check_answer(ANS, "You have 30 days.") == "ok"
    assert check_answer(ANS, "You have 30 weeks.") == "wrong_answer"
    assert check_answer(ANS, "45 days") == "wrong_answer"


def test_refusal_on_answerable_is_wrong_refusal():
    assert check_answer(ANS, "NOT_FOUND") == "wrong_refusal"
    assert check_answer(ANS, "  NOT_FOUND\n") == "wrong_refusal"


def test_unanswerable_must_be_exact_refusal():
    assert check_answer(UNANS, "NOT_FOUND") == "ok"
    assert check_answer(UNANS, "We sell stoves.") == "false_answer"


def test_matching_is_case_insensitive():
    assert check_answer({"answerable": True, "expect": [["SKU BH-1"]]}, "the sku bh-1") == "ok"


def test_regex_alternative_anchors_on_word():
    assert check_answer(YESNO, "No, we are not.") == "ok"
    assert check_answer(YESNO, "no") == "ok"
    assert check_answer(YESNO, "Nope") == "wrong_answer"
    assert check_answer(YESNO, "I know") == "wrong_answer"
    assert check_answer(YESNO, "The store is closed") == "ok"


def test_load_checks_filters_split(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text("".join(json.dumps({"id": i, "split": s}) + "\n" for i, s in enumerate(["dev", "test", "dev"])))
    assert len(load_checks(p)) == 3
    assert [r["id"] for r in load_checks(p, "dev")] == [0, 2]


def test_evaluate_counts_outcomes():
    rows = [
        {"id": "1", "question": "a", **ANS},
        {"id": "2", "question": "b", **ANS},
        {"id": "3", "question": "c", **ANS},
        {"id": "4", "question": "d", **UNANS},
        {"id": "5", "question": "e", **UNANS},
    ]
    answers = {"a": "30 days", "b": "NOT_FOUND", "c": "1 year", "d": "NOT_FOUND", "e": "yes we do"}
    res = evaluate(rows, answers.__getitem__)
    assert (res["n"], res["n_answerable"], res["n_unanswerable"]) == (5, 3, 2)
    assert res["correct_answerable"] == 1 and res["wrong_refusal"] == 1 and res["wrong_answer"] == 1
    assert res["correct_unanswerable"] == 1 and res["false_answer"] == 1
    assert [r["outcome"] for r in res["results"]] == ["ok", "wrong_refusal", "wrong_answer", "ok", "false_answer"]
