# LLD — Honest Semantic Cache (Project 03)

## Data models
```python
class CacheEntry(BaseModel):     # stored as Chroma document + metadata
    id: str                      # uuid4
    question: str                # Chroma document
    answer: str
    tenant: str
    policy_version: str
    embedder_id: str
    created_at: float            # epoch seconds

class Hit(BaseModel):
    entry: CacheEntry
    similarity: float
    age_s: float
    path: Literal["high","verified"]

class Record(BaseModel):
    question: str
    answer: str
    decision: Literal["hit","miss","bypass_personal","miss_expired","miss_entity","miss_verifier"]
    similarity: float | None
    matched_question: str | None
    latency_ms: float
    llm_ms: float | None
    model_tag: str
```

## Modules and signatures
| Module | Signature | Notes |
|---|---|---|
| gates.py | `is_personal(q: str) -> bool`; `write_back_ok(q, a, policy) -> tuple[bool, str]` | Regex + keyword rules |
| entity.py | `extract_entities(text) -> set[str]`; `entities_compatible(q_new, q_cached) -> bool` | Numbers, durations, IDs, dates, product names from policy |
| cache.py | `lookup(q, tenant, policy_version, k=3) -> list[(CacheEntry, sim)]`; `store(q, a, ...)`; `invalidate_similar(topic, min_sim) -> int`; `clear()` | Cosine similarity; Chroma PersistentClient at `cache/` |
| verifier.py | `same_intent(q_new, q_cached) -> tuple[bool, float]` | Two implementations behind one interface |
| llm.py | `answer(question, policy) -> LLMAnswer(text, ms)` | Prompt: answer only from policy; short sentences |
| service.py | `ask(q, tenant="demo") -> Record` | Implements HLD flow |
| sweep.py | `sweep(pairs, highs, lows) -> list[SweepRow]` | Reports hit rate, false hits, near-miss false hits per (LOW, HIGH) |
| replay.py | `replay(traffic) -> list[Record]` | Fresh cache per run |
| report.py | `request_stats(rows, cache_entries)`, `comparison_rows(results_dir)`, `markdown_table(rows)`, `plot_lines(...)`, `plot_cumulative(...)`, `build_report(results_dir, log_db, out_dir)` | Headless matplotlib (lazy import). Reads saved `eval/results/*.json` and the request log; writes `report/` |
| api.py | `create_app(factory, settings)`; `POST /ask {question, tenant?}`; `POST /admin/invalidate {topic, min_sim?}` -> `{deleted, min_sim, guaranteed: false, note}`; `GET /stats` | Service built on first request (import loads no model). One lock serialises requests. `/admin/invalidate` needs `X-Admin-Token` only if `SCACHE_ADMIN_TOKEN` is set |

## Decision rule (exact)
1. `is_personal` → bypass.
2. Top candidate sim < LOW → miss.
3. Expired (TTL) → miss_expired.
4. LOW ≤ sim < HIGH and verifier says different → miss_verifier.
5. `entities_compatible` false → miss_entity.
6. Otherwise hit.
Order matters: cheap checks first.

## Calibration
Use Set A calibration split (70%). Choose the (LOW, HIGH) pair with the most hits subject to zero near-miss false hits
and ≤ 1% false hits. Report on the held-out 30%. Store chosen values + date in DECISION_LOG.
