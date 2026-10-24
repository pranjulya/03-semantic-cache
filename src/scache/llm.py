import re
import subprocess
import time

import httpx
from pydantic import BaseModel

from scache.config import Settings, settings as default_settings

NOT_FOUND = "NOT_FOUND"

_V1 = """You are a support assistant for a store. Answer using ONLY the policy below.
Rules:
- Answer in short, plain sentences.
- Do not add facts that are not in the policy.
- If the policy does not answer the question, reply with exactly {not_found} and nothing else.

Policy:
{policy}

Question: {question}
Answer:"""

_V2 = """You are a support assistant for a store. Answer using ONLY the policy below.
Rules:
- Answer in one or two short, plain sentences.
- The policy often settles a question indirectly. Apply its rules: compare numbers against limits, check closing days and hours, do simple arithmetic.
- If the policy says something is not allowed, not offered, or closed, say so plainly.
- Do not add facts that are not in the policy.
- Reply with exactly {not_found} and nothing else only when nothing in the policy bears on the question.

Policy:
{policy}

Question: {question}
Answer:"""

# v3 = v2 + worked examples. The examples use a different, made-up store so they carry no facts of this
# policy (a changed policy must not be contradicted by the prompt).
_V3 = """You are a support assistant for a store. Answer using ONLY the policy below.
Rules:
- Answer in one or two short, plain sentences.
- The policy often settles a question indirectly. Apply its rules: compare numbers against limits, check closing days and hours, do simple arithmetic.
- If the policy says something is not allowed, not offered, or closed, say so plainly.
- Do not add facts that are not in the policy.
- Reply with exactly {not_found} and nothing else only when nothing in the policy bears on the question.

Examples (a different store):
Policy: Gift cards range from $20 to $150. The shop is closed on Mondays. Sales tax is 8 percent. We sell tents and packs.
Question: Can I buy a $175 gift card?
Answer: No. Gift cards go up to $150.
Question: Are you open on Monday?
Answer: No, the shop is closed on Mondays.
Question: How much tax is added to a $25 item?
Answer: $2, because sales tax is 8 percent.
Question: Do you sell kayaks?
Answer: {not_found}

Policy:
{policy}

Question: {question}
Answer:"""

_V4 = """You are a support assistant for a store. Answer using ONLY the policy below.
Rules:
- Answer in one or two short, plain sentences.
- For a yes/no question, begin with Yes or No, then give the reason from the policy.
- The policy often settles a question indirectly. Apply its rules: compare numbers against limits, check closing days and hours, do simple arithmetic.
- If the policy says something is not allowed, not offered, or closed, say so plainly.
- Do not add facts that are not in the policy.
- Reply with exactly {not_found} and nothing else only when nothing in the policy bears on the question.

Examples (a different store):
Policy: Gift cards range from $20 to $150. The shop is closed on Mondays and open 10 am to 6 pm on other days. Sales tax is 8 percent. We sell tents and packs. Support is by phone on weekdays.
Question: Can I buy a $175 gift card?
Answer: No. Gift cards go up to $150.
Question: Can I buy a $40 gift card?
Answer: Yes. Gift cards range from $20 to $150.
Question: Are you open on Monday?
Answer: No, the shop is closed on Mondays.
Question: Are you open at 11 am on Friday?
Answer: Yes, the shop is open 10 am to 6 pm on Fridays.
Question: How much tax is added to a $25 item?
Answer: $2, because sales tax is 8 percent.
Question: Can I call support on Sunday?
Answer: No. Phone support is only on weekdays.
Question: Do you sell kayaks?
Answer: {not_found}

Policy:
{policy}

Question: {question}
Answer:"""

PROMPTS = {"v1": _V1, "v2": _V2, "v3": _V3, "v4": _V4}
PROMPT = _V1  # kept for callers that import the original


class LLMError(Exception):
    """Ollama unreachable, timed out, or returned an HTTP error."""


class LLMAnswer(BaseModel):
    text: str
    ms: float

    @property
    def refused(self) -> bool:
        return self.text == NOT_FOUND


def build_prompt(question: str, policy: str, version: str | None = None) -> str:
    return PROMPTS[version or default_settings.prompt_version].format(
        not_found=NOT_FOUND, policy=policy, question=question)


class OllamaClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or default_settings
        self._http = httpx.Client(
            base_url=self.settings.ollama_url, timeout=self.settings.timeout_s, transport=transport
        )

    def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        s = self.settings
        body = {
            "model": s.llm_tag,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": s.temperature, "num_ctx": s.num_ctx},
        }
        if max_tokens is not None:
            body["options"]["num_predict"] = max_tokens
        try:
            resp = self._http.post("/api/generate", json=body)
            resp.raise_for_status()
            return resp.json()["response"].strip()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e

    def answer(self, question: str, policy: str) -> LLMAnswer:
        t0 = time.perf_counter()
        text = self.generate(build_prompt(question, policy, self.settings.prompt_version))
        return LLMAnswer(text=text, ms=(time.perf_counter() - t0) * 1000)

    def model_digest(self) -> str | None:
        try:
            resp = self._http.get("/api/tags")
            resp.raise_for_status()
            return next((m["digest"] for m in resp.json()["models"] if m["name"] == self.settings.llm_tag), None)
        except (httpx.HTTPError, KeyError, ValueError):
            return None


def parse_ps_rss_mb(ps_output: str) -> float | None:
    """Sum RSS (MB) of Ollama model-runner processes from `ps -axo rss=,command=` output."""
    total, seen = 0, False
    for line in ps_output.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if m and ("llama-server" in m[2] or "ollama runner" in m[2]):
            total += int(m[1])
            seen = True
    return total / 1024 if seen else None


def runner_rss_mb() -> float | None:
    """RAM of the Ollama runner (a separate process, so psutil on this process misses it)."""
    try:
        out = subprocess.run(["ps", "-axo", "rss=,command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ps_rss_mb(out)
