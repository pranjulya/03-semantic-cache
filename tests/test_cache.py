import pytest

from fakes import FakeEmbedder, unit_at
from scache.cache import ExactCache, SemanticCache, normalize
from scache.models import Scope

S = Scope(tenant="demo", policy_version="v1", prompt_version="v4", model_tag="m")


def test_normalize_only_casefolds_and_collapses_whitespace():
    assert normalize("  How   LONG?\n") == "how long?"
    assert normalize("How long") != normalize("How long?")


def test_exact_hit_and_miss():
    c = ExactCache()
    assert c.lookup("How long?", S) is None
    e = c.store("How long?", "30 days", S)
    assert c.lookup("how  long?", S) == e
    assert c.lookup("How long", S) is None
    assert c.count() == 1


@pytest.mark.parametrize("field,value", [("tenant", "other"), ("policy_version", "v2"),
                                         ("prompt_version", "v1"), ("model_tag", "x")])
def test_exact_scope_isolates(field, value):
    c = ExactCache()
    c.store("q", "a", S)
    assert c.lookup("q", S.model_copy(update={field: value})) is None


def test_exact_clear():
    c = ExactCache()
    c.store("q", "a", S)
    c.clear()
    assert c.count() == 0 and c.lookup("q", S) is None


@pytest.fixture
def cache(tmp_path):
    emb = FakeEmbedder({"a": unit_at(0), "b": unit_at(10), "c": unit_at(60), "d": unit_at(90)})
    return SemanticCache(emb, tmp_path / "chroma")


def test_semantic_empty_lookup(cache):
    assert cache.lookup("a", S) == []


def test_semantic_store_then_lookup_returns_similarity_and_entry(cache):
    stored = cache.store("a", "answer A", S)
    ((entry, sim),) = cache.lookup("b", S)
    assert entry.id == stored.id and entry.answer == "answer A" and entry.question == "a"
    assert entry.embedder_id == "fake-embedder" and entry.scope == S and entry.created_at == pytest.approx(stored.created_at, abs=1e-3)
    assert sim == pytest.approx(0.9848, abs=1e-3)  # cos 10 degrees


def test_semantic_orders_best_first_and_limits_k(cache):
    cache.store("a", "A", S)
    cache.store("c", "C", S)
    cache.store("d", "D", S)
    got = cache.lookup("b", S, k=2)
    assert [e.answer for e, _ in got] == ["A", "C"]
    assert got[0][1] > got[1][1]
    assert len(cache.lookup("b", S, k=10)) == 3


@pytest.mark.parametrize("field,value", [("tenant", "other"), ("policy_version", "v2"),
                                         ("prompt_version", "v1"), ("model_tag", "x")])
def test_semantic_scope_isolates(cache, field, value):
    cache.store("a", "A", S)
    assert cache.lookup("a", S.model_copy(update={field: value})) == []


def test_semantic_skips_entries_from_another_embedder(tmp_path):
    path = tmp_path / "chroma"
    old = FakeEmbedder({"a": unit_at(0)})
    old.model_id = "old-embedder"
    SemanticCache(old, path).store("a", "A", S)
    new = FakeEmbedder({"a": unit_at(0)})
    assert SemanticCache(new, path).lookup("a", S) == []


def test_semantic_vec_argument_avoids_embedding(cache):
    vec = cache.embedder.encode(["a"])[0]
    calls = cache.embedder.calls
    cache.store("a", "A", S, vec=vec)
    cache.lookup("a", S, vec=vec)
    assert cache.embedder.calls == calls


def test_semantic_persists_and_clears(tmp_path):
    emb = FakeEmbedder({"a": unit_at(0)})
    path = tmp_path / "chroma"
    SemanticCache(emb, path).store("a", "A", S)
    again = SemanticCache(emb, path)
    assert again.count() == 1
    again.clear()
    assert again.count() == 0 and again.lookup("a", S) == []


def test_semantic_delete_removes_entries(cache):
    a = cache.store("a", "A", S)
    b = cache.store("b", "B", S)
    cache.delete([a.id])
    assert [e.id for e, _ in cache.lookup("a", S)] == [b.id]
    cache.delete([])


def test_exact_delete():
    c = ExactCache()
    e = c.store("q", "A", S)
    c.delete([e.id])
    assert c.lookup("q", S) is None and c.count() == 0


def test_invalidate_similar_deletes_by_similarity_across_policy_versions(cache):
    cache.store("a", "A", S)
    cache.store("b", "B", S.model_copy(update={"policy_version": "v9"}))  # 10 degrees from a
    cache.store("c", "C", S)  # 60 degrees from a
    assert cache.invalidate_similar("a", min_sim=0.9, tenant=S.tenant) == 2
    assert cache.count() == 1
    assert cache.lookup("c", S)[0][0].answer == "C"


def test_invalidate_similar_leaves_other_tenants_alone(cache):
    cache.store("a", "A", S.model_copy(update={"tenant": "other"}))
    assert cache.invalidate_similar("a", min_sim=0.5, tenant=S.tenant) == 0
    assert cache.count() == 1


def test_invalidate_similar_on_empty_cache(cache):
    assert cache.invalidate_similar("a", min_sim=0.5) == 0
