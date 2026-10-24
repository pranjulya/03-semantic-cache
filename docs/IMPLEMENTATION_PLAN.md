# Implementation Plan — Honest Semantic Cache (Project 03)

## M0 — Setup {#m0}
Files: `pyproject.toml`, `config.py`, `embed.py`, `llm.py`, smoke tests. Done: embed + LLM run offline; latency/RAM logged.

## M1 — Policy, eval sets, no-cache baseline
Files: `data/policy.md`, `eval/*.jsonl`, `service.py` (LLM only), `logstore.py`. Done: baseline latency for Set B.

## M2 — Exact-match cache, then single-threshold semantic cache
Files: `cache.py`, `service.py`. Done: replay results for both; first false hits observed and logged.

## M3 — Sweep and calibration
Files: `sweep.py`, `cli.py sweep`. Done: threshold table; held-out report; values in DECISION_LOG.

## M4 — Entity guard
Files: `entity.py` + tests per entity type. Done: near-miss false hits = 0 on held-out.

## M5 — Gray-zone verifier
Files: `verifier.py` (two implementations). Done: comparison table (accuracy, ms) on both tiers; one chosen.

## M6 — Gates, TTL, invalidation
Files: `gates.py`, `cache.py`. Done: Set C and security checks pass.

## M7 — API, report, charts
Files: `api.py`, `report.py`. Done: charts generated; /stats works.

## M8 — Packaging + content capture
Files: README (limitations, one failure), demo script. Done: offline demo; fresh-clone setup with one command.
