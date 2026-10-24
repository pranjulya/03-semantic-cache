"""M0 smoke: embedder and LLM run offline; latency and RAM printed (run with -s to see)."""
import time

import psutil
import pytest

from scache.embed import Embedder
from scache.llm import OllamaClient, runner_rss_mb

pytestmark = pytest.mark.smoke


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / 1e6


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def test_embedder_loads_offline_and_separates_paraphrase():
    t0 = time.perf_counter()
    emb = Embedder()
    load_ms = _ms(t0)
    emb.encode(["warm up"])
    t0 = time.perf_counter()
    v = emb.encode([
        "How long do I have to return an item?",
        "What is the return window?",
        "Do you ship to Canada?",
    ])
    enc_ms = _ms(t0)
    para, other = float(v[0] @ v[1]), float(v[0] @ v[2])
    print(f"\nembedder {emb.model_id} load={load_ms:.0f}ms encode(3)={enc_ms:.1f}ms "
          f"sim paraphrase={para:.3f} other={other:.3f} rss={_rss_mb():.0f}MB")
    assert abs(float(v[0] @ v[0]) - 1) < 1e-3
    assert para > other


def test_llm_answers_from_policy():
    client = OllamaClient()
    t0 = time.perf_counter()
    client.generate("Say OK.")  # cold load, excluded from steady-state latency
    cold_ms = _ms(t0)
    policy = "Returns: unworn items can be returned within 30 days of delivery for a full refund."
    warm = [client.answer("How many days do I have to return an item?", policy) for _ in range(3)]
    ms = sorted(a.ms for a in warm)
    print(f"\nllm {client.settings.llm_tag} digest={(client.model_digest() or '?')[:12]} cold={cold_ms:.0f}ms "
          f"warm p50={ms[1]:.0f}ms max={ms[-1]:.0f}ms runner_rss={runner_rss_mb()}MB answer={warm[0].text!r}")
    assert "30" in warm[0].text
