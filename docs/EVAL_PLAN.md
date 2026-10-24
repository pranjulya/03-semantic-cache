# Eval Plan — Honest Semantic Cache (Project 03)

## Set A — labelled pairs (150)
| Type | Count | Expected |
|---|---|---|
| True paraphrase | 50 | hit |
| Different intent, similar topic | 50 | miss |
| Near-miss (number/ID/date/product differs) | 50 | miss |
Split 70/30, stratified.

## Set B — traffic replays (200 questions each)
Repeat rates 10%, 30%, 60%. Each question tagged with intent for false-hit detection.
Main chart: hit rate and median latency vs repeat rate.

## Set C — staleness (30)
Policy v1 answers cached; policy v2 changes 10 facts. 30 questions about changed and unchanged facts.
Expected: zero v1 answers served for changed facts.

## Security checks (10)
5 personal questions (must bypass). 5 poisoning attempts (instructions inside questions; answers must not be cached if the gate rejects).

## Metrics
False-hit rate, near-miss false hits, hit rate, p50/p95 latency for hit and miss, LLM compute seconds saved, peak RAM.

## Known trap (teach it)
Zero false hits in a replay can be luck: dangerous pairs may never meet in arrival order. Set A tests pairs directly.
