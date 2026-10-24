import json
import re
from collections.abc import Callable
from pathlib import Path

from scache.llm import NOT_FOUND


def load_checks(path: Path | str, split: str | None = None) -> list[dict]:
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    return [r for r in rows if split in (None, r["split"])]


def _matches(alt: str, text: str) -> bool:
    """`re:<pattern>` is a case-insensitive regex search; anything else is a case-insensitive substring."""
    if alt.startswith("re:"):
        return re.search(alt[3:], text.strip(), re.I) is not None
    return alt.lower() in text.lower()


def check_answer(row: dict, text: str) -> str:
    """Outcome of one answer: 'ok', 'wrong_refusal', 'wrong_answer' (answerable) or 'false_answer' (unanswerable)."""
    refused = text.strip() == NOT_FOUND
    if not row["answerable"]:
        return "ok" if refused else "false_answer"
    if refused:
        return "wrong_refusal"
    return "ok" if all(any(_matches(alt, text) for alt in group) for group in row["expect"]) else "wrong_answer"


def evaluate(rows: list[dict], answer_fn: Callable[[str], str]) -> dict:
    """Run every question through `answer_fn(question) -> text` and count outcomes."""
    results = []
    for r in rows:
        text = answer_fn(r["question"])
        results.append({"id": r["id"], "question": r["question"], "answer": text, "outcome": check_answer(r, text)})
    answerable = [r for r in rows if r["answerable"]]
    outcome = {x["id"]: x["outcome"] for x in results}
    n_ans, n_un = len(answerable), len(rows) - len(answerable)
    count = lambda o: sum(1 for v in outcome.values() if v == o)  # noqa: E731
    return {
        "n": len(rows), "n_answerable": n_ans, "n_unanswerable": n_un,
        "correct_answerable": sum(outcome[r["id"]] == "ok" for r in answerable),
        "wrong_refusal": count("wrong_refusal"), "wrong_answer": count("wrong_answer"),
        "correct_unanswerable": n_un - count("false_answer"), "false_answer": count("false_answer"),
        "results": results,
    }
