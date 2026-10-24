import time
from pathlib import Path
from typing import Literal

from scache.cache import ExactCache, SemanticCache
from scache.entity import entities_compatible
from scache.gates import is_personal, write_back_ok
from scache.config import Settings, settings as default_settings
from scache.llm import NOT_FOUND, OllamaClient
from scache.logstore import LogStore
from scache.models import Record, Scope
from scache.verifier import Verifier

Mode = Literal["none", "exact", "semantic"]


def load_policy(path: Path | str) -> str:
    return Path(path).read_text()


class Service:
    """The pipeline. M2: mode "none" (LLM only), "exact" (dict) or "semantic" (one threshold).

    M6: personal questions bypass the cache (no lookup, no store); entries older than the TTL are deleted and count as a
    miss; an answer is cached only if `write_back_ok` agrees.
    """

    def __init__(self, client: OllamaClient | None = None, policy: str | None = None,
                 log: LogStore | None = None, settings: Settings | None = None, run_id: str | None = None,
                 mode: Mode = "none", cache: ExactCache | SemanticCache | None = None,
                 threshold: float | None = None, guard: bool = True, fallback: bool = True,
                 verifier: Verifier | None = None, low: float | None = None, verify_high: bool = False,
                 ttl_s: float | None = None, clock=time.time, gates: bool = True):
        self.settings = settings or default_settings
        self.client = client or OllamaClient(self.settings)
        self.policy = policy if policy is not None else load_policy(self.settings.policy_path)
        self.log = log
        self.run_id = run_id
        self.mode = mode
        self.cache = cache
        self.threshold = threshold
        self.guard = guard  # entity guard on every candidate hit
        self.fallback = fallback  # if the best candidate fails the guard, try the next ones that still clear the threshold
        self.ttl_s = self.settings.ttl_s if ttl_s is None else ttl_s  # older entries count as a miss (miss_expired)
        self.gates = gates  # False reproduces M2-M5: no personal bypass, write-back only refuses empty answers and refusals
        self.clock = clock  # epoch seconds; tests and the staleness demo move it
        # M5: with a verifier, candidates in [low, threshold) are sent to it; verify_high also sends the ones >= threshold.
        self.verifier = verifier
        self.low = low
        self.verify_high = verify_high
        if verifier is not None and (low is None or low >= (threshold or 0)):
            raise ValueError("a verifier needs low < threshold")
        if mode != "none" and cache is None:
            raise ValueError(f"mode {mode!r} needs a cache")
        if mode == "semantic" and threshold is None:
            raise ValueError("semantic mode needs a threshold")

    def _scope(self, tenant: str | None) -> Scope:
        s = self.settings
        return Scope(tenant=tenant or s.tenant, policy_version=s.policy_version,
                     prompt_version=s.prompt_version, model_tag=s.llm_tag)

    def ask(self, question: str, tenant: str | None = None, intent: str | None = None) -> Record:
        """`intent` is an evaluation tag that is only written to the log."""
        t0 = time.perf_counter()
        scope = self._scope(tenant)
        sim: float | None = None
        vec = None
        decision = "miss"

        if self.mode != "none" and self.gates and is_personal(question):
            ans = self.client.answer(question, self.policy)
            return self._finish(t0, scope, intent, question, ans.text, "bypass_personal", None, None, ans.ms,
                                stored=False, reason="personal")

        if self.mode == "exact":
            entry = self.cache.lookup(question, scope)
            if entry and self._expired(entry):
                self.cache.delete([entry.id])
                decision, entry = "miss_expired", None
            if entry:
                return self._finish(t0, scope, intent, question, entry.answer, "hit", 1.0, entry.question, None)
        elif self.mode == "semantic":
            vec = self.cache.embedder.encode([question])[0]
            cands = self.cache.lookup(question, scope, k=3, vec=vec)
            if cands:
                sim = cands[0][1]
            floor = self.low if self.verifier else self.threshold
            over = [(e, c) for e, c in cands if c >= floor]
            fresh = [(e, c) for e, c in over if not self._expired(e)]
            if len(fresh) < len(over):
                self.cache.delete([e.id for e, _ in over if self._expired(e)])
                decision = "miss_expired"
            for entry, cand_sim in (fresh if self.fallback else fresh[:1]):
                if self.guard and not entities_compatible(question, entry.question):
                    decision = "miss_entity"
                    continue
                if self.verifier and (cand_sim < self.threshold or self.verify_high):
                    same, _ = self.verifier.same_intent(question, entry.question)
                    if not same:
                        decision = "miss_verifier"
                        continue
                return self._finish(t0, scope, intent, question, entry.answer, "hit", cand_sim, entry.question, None)

        ans = self.client.answer(question, self.policy)
        stored, reason = None, None
        if self.mode != "none":
            stored, reason = (write_back_ok(question, ans.text, self.policy) if self.gates else
                              (bool(ans.text) and ans.text != NOT_FOUND, "gates_off"))
            if stored:
                if self.mode == "semantic":
                    self.cache.store(question, ans.text, scope, vec=vec)
                else:
                    self.cache.store(question, ans.text, scope)
        return self._finish(t0, scope, intent, question, ans.text, decision, sim, None, ans.ms,
                            stored=stored, reason=reason)

    def _expired(self, entry) -> bool:
        return self.clock() - entry.created_at > self.ttl_s

    def _finish(self, t0, scope, intent, question, answer, decision, sim, matched, llm_ms,
                stored=None, reason=None) -> Record:
        rec = Record(
            question=question, answer=answer, decision=decision, similarity=sim, matched_question=matched,
            latency_ms=(time.perf_counter() - t0) * 1000, llm_ms=llm_ms, model_tag=scope.model_tag,
            stored=stored, store_reason=reason,
        )
        if self.log:
            self.log.log(rec, run_id=self.run_id, tenant=scope.tenant,
                         policy_version=scope.policy_version, intent=intent)
        return rec
