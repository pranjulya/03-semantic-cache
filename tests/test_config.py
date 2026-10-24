from scache.config import Settings


def test_defaults():
    s = Settings()
    assert s.ollama_url == "http://localhost:11434"
    assert s.temperature == 0
    assert s.timeout_s > 0
    assert 0 < s.low < s.high <= 1
    assert s.ttl_s > 0


def test_env_override(monkeypatch):
    monkeypatch.setenv("SCACHE_LLM_TAG", "foo:1b")
    monkeypatch.setenv("SCACHE_HIGH", "0.9")
    s = Settings()
    assert s.llm_tag == "foo:1b"
    assert s.high == 0.9
