import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder, unit_at
from scache.api import API_RUN_ID, create_app
from scache.cache import SemanticCache
from scache.config import Settings
from scache.llm import OllamaClient
from scache.logstore import LogStore
from scache.service import Service

QA, QB, QC = "How long do refunds take?", "When will the refund arrive?", "Do you ship to Mars?"
VECS = {QA: unit_at(0), QB: unit_at(5), QC: unit_at(90)}  # QA/QB cosine 0.996, QC unrelated


def make_app(tmp_path, handler=None, **settings_kw):
    s = Settings(llm_tag="m", tenant="demo", **settings_kw)
    handler = handler or (lambda req: httpx.Response(200, json={"response": "5 business days"}))
    client = OllamaClient(s, transport=httpx.MockTransport(handler))
    cache = SemanticCache(FakeEmbedder(VECS), tmp_path / "chroma")
    log = LogStore(":memory:")
    svc = Service(client=client, policy="Refunds take 5 business days.", settings=s, log=log, run_id=API_RUN_ID,
                  mode="semantic", cache=cache, threshold=0.93)
    return TestClient(create_app(lambda: svc, settings=s)), svc


def test_ask_miss_then_hit(tmp_path):
    c, _ = make_app(tmp_path)
    first = c.post("/ask", json={"question": QA}).json()
    second = c.post("/ask", json={"question": QB}).json()
    assert first["decision"] == "miss" and first["stored"] is True
    assert second["decision"] == "hit" and second["matched_question"] == QA
    assert second["answer"] == "5 business days"


def test_ask_rejects_empty_question(tmp_path):
    c, _ = make_app(tmp_path)
    assert c.post("/ask", json={"question": ""}).status_code == 422


def test_ask_llm_down_is_503(tmp_path):
    c, _ = make_app(tmp_path, lambda req: httpx.Response(500))
    r = c.post("/ask", json={"question": QA})
    assert r.status_code == 503 and "LLM unavailable" in r.json()["detail"]


def test_invalidate_reports_count_and_no_guarantee(tmp_path):
    c, svc = make_app(tmp_path)
    c.post("/ask", json={"question": QA})
    r = c.post("/admin/invalidate", json={"topic": QB, "min_sim": 0.9}).json()
    assert r["deleted"] == 1 and r["guaranteed"] is False and "Best effort" in r["note"]
    assert svc.cache.count() == 0


def test_invalidate_uses_default_min_sim_and_misses_unrelated(tmp_path):
    c, svc = make_app(tmp_path)
    c.post("/ask", json={"question": QA})
    r = c.post("/admin/invalidate", json={"topic": QC}).json()
    assert r["min_sim"] == 0.85 and r["deleted"] == 0
    assert svc.cache.count() == 1


def test_invalidate_min_sim_out_of_range(tmp_path):
    c, _ = make_app(tmp_path)
    assert c.post("/admin/invalidate", json={"topic": QA, "min_sim": 0}).status_code == 422
    assert c.post("/admin/invalidate", json={"topic": QA, "min_sim": 1.5}).status_code == 422


def test_invalidate_needs_token_when_configured(tmp_path):
    c, _ = make_app(tmp_path, admin_token="s3cret")
    body = {"topic": QA}
    assert c.post("/admin/invalidate", json=body).status_code == 401
    assert c.post("/admin/invalidate", json=body, headers={"X-Admin-Token": "nope"}).status_code == 401
    assert c.post("/admin/invalidate", json=body, headers={"X-Admin-Token": "s3cret"}).status_code == 200


def test_stats_counts_requests(tmp_path):
    c, _ = make_app(tmp_path)
    c.post("/ask", json={"question": QA})
    c.post("/ask", json={"question": QB})
    s = c.get("/stats").json()
    assert s["requests"] == 2 and s["decisions"] == {"hit": 1, "miss": 1}
    assert s["hit_rate"] == 0.5 and s["cache_entries"] == 1
    assert s["policy_version"] == "v1" and s["model"] == "m"


def test_stats_empty(tmp_path):
    c, _ = make_app(tmp_path)
    s = c.get("/stats").json()
    assert s["requests"] == 0 and s["hit_rate"] is None and s["llm_seconds_avoided_estimate"] == 0.0

