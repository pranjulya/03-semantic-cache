"""HTTP API. `uvicorn scache.api:app` builds the service on the first request, so importing this module loads no model."""
import secrets
import threading
from collections.abc import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from scache.cache import SemanticCache
from scache.config import Settings, settings as default_settings
from scache.llm import LLMError
from scache.report import request_stats
from scache.service import Service

API_RUN_ID = "api"


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tenant: str | None = None


class AskOut(BaseModel):
    answer: str
    decision: str
    similarity: float | None
    matched_question: str | None
    latency_ms: float
    stored: bool | None


class InvalidateIn(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    min_sim: float | None = Field(default=None, gt=0, le=1)
    tenant: str | None = None


class InvalidateOut(BaseModel):
    deleted: int
    min_sim: float
    guaranteed: bool
    note: str


INVALIDATE_NOTE = ("Best effort: deletes cached questions similar to the topic. On the staleness set the default setting "
                   "left 9 of 20 stale answers in place. After a policy change bump policy_version instead.")


def build_service(settings: Settings) -> Service:
    from scache.embed import Embedder
    from scache.llm import OllamaClient
    from scache.logstore import LogStore
    from scache.verifier import LLMJudgeVerifier

    client = OllamaClient(settings)
    cache = SemanticCache(Embedder(), settings.cache_dir)
    return Service(client=client, settings=settings, log=LogStore(settings.log_db), run_id=API_RUN_ID, mode="semantic",
                   cache=cache, threshold=settings.high, low=settings.low, verify_high=settings.verify_high,
                   verifier=LLMJudgeVerifier(client))


def create_app(factory: Callable[[], Service] | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings
    factory = factory or (lambda: build_service(settings))
    app = FastAPI(title="Honest Semantic Cache")
    state: dict = {"svc": None}
    lock = threading.Lock()  # one request at a time: one local LLM, and Chroma and SQLite are shared

    def svc() -> Service:
        if state["svc"] is None:
            state["svc"] = factory()
        return state["svc"]

    @app.post("/ask", response_model=AskOut)
    def ask(body: AskIn) -> AskOut:
        with lock:
            try:
                rec = svc().ask(body.question, tenant=body.tenant)
            except LLMError as e:
                raise HTTPException(status_code=503, detail=f"LLM unavailable: {e}")
        return AskOut(answer=rec.answer, decision=rec.decision, similarity=rec.similarity,
                      matched_question=rec.matched_question, latency_ms=rec.latency_ms, stored=rec.stored)

    @app.post("/admin/invalidate", response_model=InvalidateOut)
    def invalidate(body: InvalidateIn, x_admin_token: str | None = Header(default=None)) -> InvalidateOut:
        if settings.admin_token and not secrets.compare_digest(x_admin_token or "", settings.admin_token):
            raise HTTPException(status_code=401, detail="bad or missing X-Admin-Token")
        min_sim = body.min_sim if body.min_sim is not None else settings.invalidate_min_sim
        with lock:
            n = svc().cache.invalidate_similar(body.topic, min_sim, body.tenant)
        return InvalidateOut(deleted=n, min_sim=min_sim, guaranteed=False, note=INVALIDATE_NOTE)

    @app.get("/stats")
    def stats() -> dict:
        with lock:
            service = svc()
            rows = service.log.rows(API_RUN_ID) if service.log else []
            return {**request_stats(rows, cache_entries=service.cache.count()),
                    "policy_version": settings.policy_version, "model": settings.llm_tag}

    return app


app = create_app()
