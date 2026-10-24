"""M6 gates: what must never be cached (personal questions) and what must not be written back (bad answers).

Both are plain rules, so they have gaps. Rules lean towards "do not cache": a wrongly bypassed question costs one
LLM call, a wrongly cached one leaks or poisons.
"""
import re

from scache.entity import extract_entities
from scache.llm import NOT_FOUND

MAX_ANSWER_CHARS = 600  # the longest answer to a real policy question in the M1 runs is far below this

_PERSONAL = [
    re.compile(r"\b[A-Z]{2}-\d{5,}\b"),  # order numbers: FO-123456
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),  # e-mail address
    re.compile(r"(?<![\d$])\+?\d(?:[\s().-]?\d){9,14}(?!\d)"),  # phone number or card number: 10-15 digits
    re.compile(r"\b(?:my|our)\s+(?:\w+\s+)?(?:orders?|accounts?|packages?|parcels?|shipments?|purchases?|receipts?|"
               r"invoices?|refunds?|balance|passwords?|addresses|address|membership|points|tracking|deliver(?:y|ies)|"
               r"gift\s+cards?)\b", re.I),
]

# Instructions hidden in a question. A question that carries one is never cached, whatever the answer says.
_INJECTION = [
    re.compile(r"\b(?:ignore|disregard|forget|override)\b[^.?!]{0,40}\b(?:policy|policies|instructions?|rules?|prompt|above)\b", re.I),
    re.compile(r"\bfrom\s+now\s+on\b", re.I),
    re.compile(r"\b(?:system|developer|assistant)\s*:", re.I),
    re.compile(r"\b(?:reply|respond|answer|say|output|print)\b[^.?!]{0,20}\b(?:exactly|only|verbatim)\b", re.I),
    re.compile(r"\b(?:reveal|show|print|repeat|leak)\b[^.?!]{0,30}\b(?:prompt|instructions?|system)\b", re.I),
    re.compile(r"\bthe\s+policy\s+(?:has\s+)?changed\b", re.I),
]


def is_personal(q: str) -> bool:
    """True if the answer could depend on who asks: order numbers, e-mail, phone/card digits, "my order/account"."""
    return any(p.search(q) for p in _PERSONAL)


def has_injection(q: str) -> bool:
    return any(p.search(q) for p in _INJECTION)


def write_back_ok(q: str, a: str, policy: str) -> tuple[bool, str]:
    """May this (question, answer) be cached? Returns (ok, reason); reason is "ok" or the rule that said no."""
    if not a.strip():
        return False, "empty"
    if a.strip() == NOT_FOUND or NOT_FOUND in a:
        return False, "refusal"
    if is_personal(q):
        return False, "personal"
    if has_injection(q):
        return False, "injection"
    if len(a) > MAX_ANSWER_CHARS:
        return False, "too_long"
    if _leaks_identifier(a, policy):
        return False, "personal_answer"
    ungrounded = _ungrounded(q, a, policy)
    if ungrounded:
        return False, "ungrounded:" + ",".join(sorted(ungrounded))
    return True, "ok"


def _leaks_identifier(answer: str, policy: str) -> bool:
    """An order number, e-mail or phone number in the answer that the policy itself does not contain (the support phone
    number and the "FO-123456" format example are policy text and fine)."""
    return any(m.group(0) not in policy for p in _PERSONAL[:3] for m in p.finditer(answer))


_CHECKED = ("usd:", "pct:", "dur:", "time:")


def _ungrounded(question: str, answer: str, policy: str) -> set[str]:
    """Money, percentages, durations and times in the answer that neither the policy nor the question states. An answer
    computed from the policy (tax on $200) is also flagged: not cached, but never wrong."""
    known = extract_entities(policy) | extract_entities(question)
    return {e for e in extract_entities(answer) if e.startswith(_CHECKED) and e not in known}
