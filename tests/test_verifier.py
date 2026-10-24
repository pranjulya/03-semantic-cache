import json

import httpx

from scache.config import Settings
from scache.llm import OllamaClient
from scache.verifier import JUDGE_PROMPT, CrossEncoderVerifier, LLMJudgeVerifier


class FakeModel:
    def __init__(self, scores):
        self.scores = scores
        self.seen = []

    def predict(self, pairs):
        self.seen.append(pairs)
        return self.scores[: len(pairs)]


def test_cross_encoder_threshold():
    v = CrossEncoderVerifier(threshold=0.6, model=FakeModel([0.61]))
    assert v.same_intent("a", "b") == (True, 0.61)
    v = CrossEncoderVerifier(threshold=0.6, model=FakeModel([0.59]))
    assert v.same_intent("a", "b") == (False, 0.59)


def test_cross_encoder_passes_the_pair_in_order():
    m = FakeModel([0.9])
    CrossEncoderVerifier(model=m).same_intent("new", "cached")
    assert m.seen == [[("new", "cached")]]


def judge(reply: str):
    seen = {}

    def handler(req):
        seen["prompt"] = json.loads(req.content)["prompt"]
        return httpx.Response(200, json={"response": reply})

    client = OllamaClient(Settings(llm_tag="m"), transport=httpx.MockTransport(handler))
    return LLMJudgeVerifier(client), seen


def test_judge_yes_is_same():
    v, seen = judge(" YES")
    assert v.same_intent("q new", "q cached") == (True, 1.0)
    assert "Question 1: q new" in seen["prompt"] and "Question 2: q cached" in seen["prompt"]


def test_judge_no_is_different():
    assert judge("NO")[0].same_intent("a", "b") == (False, 0.0)


def test_judge_tolerates_case_and_punctuation():
    assert judge("Yes.")[0].same_intent("a", "b")[0] is True
    assert judge("**YES**")[0].same_intent("a", "b")[0] is True


def test_judge_accepts_the_echoed_label_and_trailing_explanation():
    assert judge("Same: YES")[0].same_intent("a", "b") == (True, 1.0)
    assert judge("YES\n\nThe questions ask the same thing.")[0].same_intent("a", "b")[0] is True
    assert judge("Same: NO")[0].same_intent("a", "b") == (False, 0.0)
    assert judge("No, but yes in part")[0].same_intent("a", "b")[0] is False


def test_judge_unclear_reply_is_different():
    for reply in ("", "Maybe", "I think they are the same", "YESTERDAY"):
        assert judge(reply)[0].same_intent("a", "b")[0] is False, reply


def test_judge_prompt_carries_no_policy_fact_values():
    from eval_data import FACTS

    for fact, values in FACTS.items():
        for value in values:
            assert value not in JUDGE_PROMPT, (fact, value)
