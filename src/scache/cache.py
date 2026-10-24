import re
import time
import uuid
from pathlib import Path

import chromadb
import numpy as np

from scache.config import settings
from scache.embed import Embedder
from scache.models import CacheEntry, Scope

COLLECTION = "answers"


def normalize(question: str) -> str:
    """Exact-match key: case-folded, whitespace collapsed. Nothing else (no punctuation or stop-word rules)."""
    return re.sub(r"\s+", " ", question.casefold()).strip()


class ExactCache:
    """M2 baseline: what a plain dict gives you. Same scope rules as the semantic cache."""

    def __init__(self) -> None:
        self._d: dict[tuple[Scope, str], CacheEntry] = {}

    def lookup(self, question: str, scope: Scope) -> CacheEntry | None:
        return self._d.get((scope, normalize(question)))

    def store(self, question: str, answer: str, scope: Scope, embedder_id: str = "exact") -> CacheEntry:
        entry = CacheEntry(id=str(uuid.uuid4()), question=question, answer=answer, scope=scope,
                           embedder_id=embedder_id, created_at=time.time())
        self._d[(scope, normalize(question))] = entry
        return entry

    def count(self) -> int:
        return len(self._d)

    def delete(self, ids: list[str]) -> None:
        gone = set(ids)
        self._d = {k: v for k, v in self._d.items() if v.id not in gone}

    def clear(self) -> None:
        self._d.clear()


class SemanticCache:
    """Chroma collection of answered questions, searched by cosine similarity inside one Scope."""

    def __init__(self, embedder: Embedder, path: Path | str | None = None):
        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=str(path or settings.cache_dir))
        self.col = self.client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    def count(self) -> int:
        return self.col.count()

    def _where(self, scope: Scope) -> dict:
        conds = {"tenant": scope.tenant, "policy_version": scope.policy_version,
                 "prompt_version": scope.prompt_version, "model_tag": scope.model_tag,
                 "embedder_id": self.embedder.model_id}
        return {"$and": [{k: v} for k, v in conds.items()]}

    def lookup(self, question: str, scope: Scope, k: int = 3, vec: np.ndarray | None = None) -> list[tuple[CacheEntry, float]]:
        """Top-k entries in `scope`, best first, as (entry, cosine similarity). Pass `vec` to skip re-embedding."""
        n = min(k, self.count())
        if n == 0:
            return []
        if vec is None:
            vec = self.embedder.encode([question])[0]
        res = self.col.query(query_embeddings=[vec.tolist()], n_results=n, where=self._where(scope),
                             include=["documents", "metadatas", "distances"])
        out = []
        for id_, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            out.append((CacheEntry(id=id_, question=doc, answer=meta["answer"], scope=scope,
                                   embedder_id=meta["embedder_id"], created_at=meta["created_at"]), 1.0 - dist))
        return out

    def store(self, question: str, answer: str, scope: Scope, vec: np.ndarray | None = None) -> CacheEntry:
        if vec is None:
            vec = self.embedder.encode([question])[0]
        entry = CacheEntry(id=str(uuid.uuid4()), question=question, answer=answer, scope=scope,
                           embedder_id=self.embedder.model_id, created_at=time.time())
        self.col.add(
            ids=[entry.id], embeddings=[vec.tolist()], documents=[question],
            metadatas=[{"answer": answer, "tenant": scope.tenant, "policy_version": scope.policy_version,
                        "prompt_version": scope.prompt_version, "model_tag": scope.model_tag,
                        "embedder_id": entry.embedder_id, "created_at": entry.created_at}],
        )
        return entry

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.col.delete(ids=ids)

    def similar(self, topic: str, tenant: str | None = None) -> list[tuple[CacheEntry, float]]:
        """Every entry of `tenant` (default: the configured one), in any scope, with its similarity to `topic`."""
        n = self.count()
        if n == 0:
            return []
        vec = self.embedder.encode([topic])[0]
        res = self.col.query(query_embeddings=[vec.tolist()], n_results=n, where={"tenant": tenant or settings.tenant},
                             include=["documents", "metadatas", "distances"])
        out = []
        for id_, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            scope = Scope(tenant=meta["tenant"], policy_version=meta["policy_version"],
                          prompt_version=meta["prompt_version"], model_tag=meta["model_tag"])
            out.append((CacheEntry(id=id_, question=doc, answer=meta["answer"], scope=scope,
                                   embedder_id=meta["embedder_id"], created_at=meta["created_at"]), 1.0 - dist))
        return out

    def invalidate_similar(self, topic: str, min_sim: float, tenant: str | None = None) -> int:
        """Delete every entry of `tenant` whose question is at least `min_sim` similar to `topic`, whatever its policy
        version. Returns how many were deleted."""
        gone = [e.id for e, sim in self.similar(topic, tenant) if sim >= min_sim]
        self.delete(gone)
        return len(gone)

    def clear(self) -> None:
        self.client.delete_collection(COLLECTION)
        self.col = self.client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
