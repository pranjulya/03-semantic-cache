import json

import httpx
import pytest

from scache.config import Settings
from scache.llm import NOT_FOUND, LLMError, OllamaClient, build_prompt, parse_ps_rss_mb


def make_client(handler):
    return OllamaClient(Settings(llm_tag="m"), transport=httpx.MockTransport(handler))


def test_answer_sends_prompt_and_returns_text_and_ms():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"response": "  Returns are accepted within 30 days.\n"})

    ans = make_client(handler).answer("Can I return shoes?", "Returns: 30 days.")
    assert ans.text == "Returns are accepted within 30 days."
    assert ans.ms >= 0
    assert not ans.refused
    assert seen["model"] == "m"
    assert seen["stream"] is False
    assert seen["options"]["temperature"] == 0
    assert seen["options"]["num_ctx"] == Settings().num_ctx
    assert "Returns: 30 days." in seen["prompt"] and "Can I return shoes?" in seen["prompt"]


def test_not_found_is_refusal():
    ans = make_client(lambda r: httpx.Response(200, json={"response": NOT_FOUND})).answer("q", "p")
    assert ans.refused


def test_prompt_names_sentinel():
    assert NOT_FOUND in build_prompt("q", "p")


def test_http_error_raises():
    with pytest.raises(LLMError):
        make_client(lambda r: httpx.Response(500, text="boom")).answer("q", "p")


def test_connect_error_raises():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError):
        make_client(handler).answer("q", "p")


def test_missing_response_field_raises():
    with pytest.raises(LLMError):
        make_client(lambda r: httpx.Response(200, json={})).answer("q", "p")


def test_model_digest():
    def handler(req):
        assert req.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "m", "digest": "abc"}, {"name": "o", "digest": "z"}]})

    assert make_client(handler).model_digest() == "abc"


def test_model_digest_unreachable_is_none():
    def handler(req):
        raise httpx.ConnectError("down")

    assert make_client(handler).model_digest() is None


def test_parse_ps_rss_mb():
    ps = "  2048 /usr/local/bin/ollama runner --model x\n  1024 /usr/bin/zsh\n  1024 /x/llama-server -m y\n"
    assert parse_ps_rss_mb(ps) == 3.0
    assert parse_ps_rss_mb("  1024 /usr/bin/zsh\n") is None


def test_every_prompt_version_has_the_placeholders():
    from scache.llm import PROMPTS

    for version, template in PROMPTS.items():
        out = template.format(not_found=NOT_FOUND, policy="POL", question="QUE")
        assert "POL" in out and "QUE" in out and NOT_FOUND in out, version


def test_default_prompt_version_exists():
    from scache.llm import PROMPTS

    assert Settings().prompt_version in PROMPTS


def test_build_prompt_uses_requested_version():
    from scache.llm import PROMPTS

    assert build_prompt("q", "p", "v1") != build_prompt("q", "p", "v4")
    assert build_prompt("q", "p", "v4") == PROMPTS["v4"].format(not_found=NOT_FOUND, policy="p", question="q")


def test_answer_uses_configured_prompt_version():
    seen = {}

    def handler(req):
        seen["prompt"] = json.loads(req.content)["prompt"]
        return httpx.Response(200, json={"response": "x"})

    client = OllamaClient(Settings(llm_tag="m", prompt_version="v1"), transport=httpx.MockTransport(handler))
    client.answer("q", "p")
    assert seen["prompt"] == build_prompt("q", "p", "v1")


def test_prompt_templates_carry_no_policy_fact_values():
    """Examples must not state values of the real policy: a changed policy (v2) must not be contradicted."""
    from eval_data import FACTS
    from scache.llm import PROMPTS

    for version, template in PROMPTS.items():
        for fact, values in FACTS.items():
            for value in values:
                assert value not in template, (version, fact, value)


def test_generate_caps_output_tokens_when_asked():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"response": "x"})

    client = make_client(handler)
    client.generate("p", max_tokens=6)
    assert seen["options"]["num_predict"] == 6
    client.generate("p")
    assert "num_predict" not in seen["options"]
