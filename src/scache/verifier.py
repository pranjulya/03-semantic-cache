"""M5 gray-zone verifier: do two questions ask for the same answer?

Two implementations behind one interface, compared in DECISION_LOG:
  CrossEncoderVerifier  a small pair classifier trained on duplicate questions (Quora), a few ms on CPU/MPS
  LLMJudgeVerifier      the local LLM asked a yes/no question, hundreds of ms
`same_intent` returns (same, score). Anything unclear counts as "different": a wrong "different" costs one LLM
call, a wrong "same" serves a wrong answer.
"""
import os
import re
from typing import Protocol

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from scache.llm import OllamaClient

CROSS_ENCODER_ID = "cross-encoder/quora-distilroberta-base"


class Verifier(Protocol):
    name: str

    def same_intent(self, q_new: str, q_cached: str) -> tuple[bool, float]: ...


class CrossEncoderVerifier:
    """Pair classifier. `model` needs `.predict(list[tuple[str, str]]) -> array of probabilities in [0, 1]`."""

    def __init__(self, threshold: float = 0.5, model=None, model_id: str = CROSS_ENCODER_ID):
        if model is None:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(model_id)
        self.model = model
        self.threshold = threshold
        self.name = f"cross-encoder:{model_id}"

    def scores(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [float(s) for s in self.model.predict(pairs)]

    def same_intent(self, q_new: str, q_cached: str) -> tuple[bool, float]:
        score = self.scores([(q_new, q_cached)])[0]
        return score >= self.threshold, score


# The examples come from a different store, so the prompt carries no fact of the real policy.
JUDGE_PROMPT = """Two customers ask a store's support assistant a question each. Decide whether ONE answer would be correct for BOTH questions.
Say NO if the questions differ in any number, amount, duration, date, day of the week, time, product, size or ID, or if they ask about a different thing (for example the price and the weight of the same item).
Say YES only if they ask for the same information about the same thing and only the wording differs.

Question 1: How long is the exchange period for a stove?
Question 2: Until when can I swap a stove for another one?
Same: YES

Question 1: Is the shop open on Monday?
Question 2: Is the shop open on Tuesday?
Same: NO

Question 1: How much is the 20L pack?
Question 2: How much does the 30L pack cost?
Same: NO

Question 1: What is the weight of the Summit kayak?
Question 2: How much does the Summit kayak cost?
Same: NO

Question 1: Do you offer gift wrapping?
Question 2: Is it possible to have an item wrapped as a gift?
Same: YES

Question 1: {a}
Question 2: {b}
Same:"""


JUDGE_MAX_TOKENS = 6  # "Same: YES" fits; any explanation after the verdict is not needed
_VERDICT = re.compile(r"\W*(?:same\W*)?(yes|no)\b", re.I)  # the model sometimes echoes the "Same:" label


class LLMJudgeVerifier:
    def __init__(self, client: OllamaClient):
        self.client = client
        self.name = f"llm-judge:{client.settings.llm_tag}"

    def same_intent(self, q_new: str, q_cached: str) -> tuple[bool, float]:
        reply = self.client.generate(JUDGE_PROMPT.format(a=q_new, b=q_cached), max_tokens=JUDGE_MAX_TOKENS)
        m = _VERDICT.match(reply)
        if m and m[1].upper() == "YES":
            return True, 1.0
        return False, 0.0
