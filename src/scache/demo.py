"""Scripted walk through the pipeline, for `cli demo`. It prints what the cache did and whether that is what we expected;
a line that says "no" is a result, not an error."""
from dataclasses import dataclass

from scache.service import Service


@dataclass(frozen=True)
class Step:
    title: str
    question: str
    expect: tuple[str, ...]  # acceptable decisions
    policy_version: str | None = None  # switch the service to this version before asking


STEPS = (
    Step("first ask: nothing cached yet", "How many days do I have to return an unused item?", ("miss",)),
    Step("paraphrase: should reuse the answer", "What is the time limit for sending back something I have not used?", ("hit",)),
    Step("exact repeat", "How many days do I have to return an unused item?", ("hit",)),
    Step("ask about Canada", "Do you ship to Canada?", ("miss",)),
    Step("near-miss: same wording, other country", "Do you ship to Mexico?", ("miss", "miss_entity", "miss_verifier")),
    Step("personal question: bypasses the cache", "Where is my order #A-48213?", ("bypass_personal",)),
    Step("policy changed to v2: old answers must not be served",
         "How many days do I have to return an unused item?", ("miss",), policy_version="v2"),
)


def run_demo(svc: Service, steps=STEPS, out=print) -> list[dict]:
    results = []
    for i, st in enumerate(steps, 1):
        if st.policy_version:
            svc.settings = svc.settings.model_copy(update={"policy_version": st.policy_version})
        rec = svc.ask(st.question)
        ok = rec.decision in st.expect
        results.append({"title": st.title, "question": st.question, "decision": rec.decision,
                        "similarity": rec.similarity, "latency_ms": rec.latency_ms, "as_expected": ok})
        sim = f" sim {rec.similarity:.2f}" if rec.similarity is not None else ""
        out(f"{i}. {st.title}\n   Q: {st.question}\n   A: {rec.answer}\n"
            f"   [{rec.decision}{sim}, {rec.latency_ms:.0f} ms] as expected: {'yes' if ok else 'NO (expected ' + '/'.join(st.expect) + ')'}")
    out(f"{sum(r['as_expected'] for r in results)}/{len(results)} steps as expected")
    return results
