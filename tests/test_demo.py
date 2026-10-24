import httpx

from fakes import FakeEmbedder, unit_at
from scache.cache import SemanticCache
from scache.config import Settings
from scache.demo import STEPS, Step, run_demo
from scache.llm import OllamaClient
from scache.service import Service


def make(tmp_path, vectors):
    s = Settings(llm_tag="m")
    client = OllamaClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"response": "30 days"})))
    return Service(client=client, policy="Returns are accepted within 30 days.", settings=s, mode="semantic", threshold=0.93,
                   cache=SemanticCache(FakeEmbedder(vectors), tmp_path / "c"))


def test_run_demo_reports_expectations_and_policy_switch(tmp_path):
    q1, q2 = "How old?", "What age?"
    steps = (Step("a", q1, ("miss",)), Step("b", q2, ("hit",)), Step("c", q1, ("hit",), policy_version="v2"))
    lines = []
    res = run_demo(make(tmp_path, {q1: unit_at(0), q2: unit_at(5)}), steps, out=lines.append)
    assert [r["decision"] for r in res] == ["miss", "hit", "miss"]
    assert [r["as_expected"] for r in res] == [True, True, False]  # step c expected a hit but v2 correctly missed
    assert "NO (expected hit)" in lines[2] and lines[-1] == "2/3 steps as expected"


def test_default_steps_are_well_formed():
    assert len(STEPS) >= 6 and all(s.expect for s in STEPS)
    assert sum(1 for s in STEPS if s.policy_version) == 1
