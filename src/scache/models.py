from typing import Literal

from pydantic import BaseModel


class Record(BaseModel):
    question: str
    answer: str
    decision: Literal["hit", "miss", "bypass_personal", "miss_expired", "miss_entity", "miss_verifier"]
    similarity: float | None
    matched_question: str | None
    latency_ms: float
    llm_ms: float | None
    model_tag: str
    stored: bool | None = None  # write-back outcome of a miss: True stored, False refused by the gate, None no cache
    store_reason: str | None = None  # "ok" or the gate rule that refused


class Scope(BaseModel, frozen=True):
    """What an answer depends on besides the question. An entry is served only inside its own scope."""
    tenant: str
    policy_version: str
    prompt_version: str
    model_tag: str


class CacheEntry(BaseModel):
    id: str
    question: str
    answer: str
    scope: Scope
    embedder_id: str
    created_at: float  # epoch seconds


class Hit(BaseModel):
    entry: CacheEntry
    similarity: float
    age_s: float
    path: Literal["high", "verified", "exact"]
