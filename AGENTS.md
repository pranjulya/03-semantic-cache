# AGENTS.md — 03 Honest Semantic Cache

## Read this first (token rules)
- Read ONLY this file plus the files listed under "Current milestone".
- Design source of truth: `docs/LLD.md`.
- Never open `cache/`, `logs/`, or `eval/results/` unless a task asks.
- If a fact is missing, ask. One milestone per session.

## Project in one paragraph
FastAPI support assistant over a fixed store policy. Question → personal-data gate → embed → Chroma top-3
(filtered by tenant + policy version) → two thresholds: HIGH = hit, LOW..HIGH = gray zone sent to a verifier,
below LOW = miss. Hits also pass an entity guard (numbers, IDs, dates must match). Misses call a local LLM;
answers pass a write-back gate before being cached. Every request is logged to SQLite for evaluation.

## Repo map
```
src/scache/
  models.py      Record (pydantic)
  config.py      thresholds HIGH/LOW, TTL, policy_version, tenant, model tags
  gates.py       is_personal(q), write_back_ok(q, a, policy)
  embed.py       Embedder
  cache.py       SemanticCache.lookup(), store(), invalidate_similar(), clear()
  entity.py      extract_entities(text), entities_compatible(a, b), entity_diff(a, b)
  verifier.py    Verifier.same_intent(q_new, q_cached) -> (bool, score)
  llm.py         OllamaClient.answer(question, policy) -> LLMAnswer; PROMPTS v1-v4, default v4
  promptcheck.py score a prompt on eval/answers.jsonl (dev/test split)
  service.py     ask(question) -> Record   (the pipeline)
  logstore.py    SQLite request log
  sweep.py       calibration: sweep (LOW, HIGH) on Set A, choose on calibration split, report held-out
  replay.py      traffic replay at different repeat rates
  report.py      tables + charts (matplotlib, headless)
  api.py         FastAPI: POST /ask, POST /admin/invalidate, GET /stats
  cli.py         ask, replay, sweep, invalidate-demo, clear
scripts/              setup.sh (one-command setup), download_models.py (one-time online step),
                      eval_data.py (hand-written eval content), make_eval.py (writes data/ policies + eval/*.jsonl)
data/policy.md        synthetic store policy v1 (own fictional store); policy_v2.md changes 10 facts (Set C)
eval/pairs.jsonl      Set A (labelled pairs)
eval/traffic_*.jsonl  Set B (10%/30%/60% repeat rates)
eval/staleness.jsonl  Set C
eval/security.jsonl   5 personal + 5 poisoning questions
eval/answers.jsonl    47 labelled answer checks for prompt tuning (dev/test)
eval/results/         saved runs (m1_baseline_*.json)
tests/
```

## Commands
```
uv sync
scripts/setup.sh                 # first time only
uv run pytest -q -m "not smoke"  # no Ollama needed
uv run pytest -q -s -m smoke     # live: embedder + Ollama, prints latency/RAM
uv run python scripts/make_eval.py   # regenerate policies + eval sets (deterministic)
uv run python -m scache.cli promptcheck --split test --model qwen2.5:7b --prompt v4 -v
uv run python -m scache.cli sweep --pairs eval/pairs.jsonl
uv run python -m scache.cli replay --traffic eval/traffic_30.jsonl --mode semantic --threshold 0.97   # modes: none | exact | semantic
uv run uvicorn scache.api:app
```

## Rules
- Runtime network: localhost Ollama only.
- Only cache answers that are identical for every user. Personal questions bypass the cache.
- Thresholds come from `sweep` on the calibration split. Never hand-edit them without a DECISION_LOG entry.
- Every cache entry stores: embedder id, policy_version, tenant, created_at.
- Report savings as latency and compute seconds, never as "dollars" for the local LLM.
- Every new function gets a pytest test. Run tests before saying "done". `tests/test_offline.py` must stay green.

## Current milestone
M0-M8 done (2026-10-24; see DECISION_LOG). Project complete; further work is outside the plan. Thresholds: HIGH 0.93, LOW 0.80; verifier = 7B LLM judge (also on hits >= HIGH); TTL 24 h. After a policy change bump `policy_version`; `invalidate_similar` is best-effort. `uv run python -m scache.cli demo` is the scripted walk-through, `report` writes `report/`.

## PB Prompt Rules
<!-- Placeholder. To be filled from the prompt file PB provides. -->
