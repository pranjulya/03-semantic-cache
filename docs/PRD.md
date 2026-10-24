# PRD — Honest Semantic Cache (Project 03)

## 1. Problem
Assistants answer the same questions many times in different words. Each answer costs LLM time.
A semantic cache can reuse answers, but a wrong reuse (false hit) gives a user a wrong answer.
Public tutorials report large savings on small synthetic traffic and tune thresholds on the same data they report.
**Evidence status:** one source (an Instagram tutorial, 10 Oct 2026). Weak signal. Teaching project.

## 2. Users
Learner (primary). Secondary: developers running local assistants with repeated questions.

## 3. Goals
- G1: Reuse answers for paraphrases with a measured false-hit rate.
- G2: Show how savings depend on the traffic repeat rate.
- G3: Handle staleness (policy change) and basic cache security.

## 4. Non-goals
Multi-tenant production scale, distributed cache, personalized answers, cloud LLM fallback.

## 5. Functional requirements
| ID | Requirement |
|---|---|
| FR1 | Personal-data gate: questions with order IDs, emails, phone numbers, or "my order/account" bypass the cache. |
| FR2 | Embed question locally; query top-3 filtered by tenant and policy_version. |
| FR3 | Decide: sim ≥ HIGH → candidate hit; LOW ≤ sim < HIGH → verifier; sim < LOW → miss. |
| FR4 | Entity guard on every candidate hit: numbers, IDs, dates, product names must be compatible. |
| FR5 | TTL expiry counts as a miss. |
| FR6 | On a miss: local LLM answers from the policy text only. |
| FR7 | Write-back gate: store only if not personal, not a refusal/error, and within length limits. |
| FR8 | Invalidation: by policy_version bump, by topic (`invalidate_similar`), or full clear. |
| FR9 | Log every request (decision, sims, latency, model tag) to SQLite. |
| FR10 | Commands: sweep, replay, report; API: /ask, /admin/invalidate, /stats. |

## 6. Non-functional requirements
Local runtime; offline test. 8 GB tier: verifier may be the lighter option. Cache lookup target: tens of ms (measure).

## 7. Success metrics (proposed)
| Metric | Proposed bar |
|---|---|
| False hits on held-out Set A | 0 near-miss false hits; report the 95% upper bound (11.4% for the 30 held-out miss pairs, so this is weak evidence). Also list every false hit on Set B |
| Near-miss (entity) false hits | 0 |
| Hit rate at 30% repeat traffic | report, no fixed bar |
| Stale answers served on Set C | 0 |
| p50 latency hit vs miss | report both |

## 8. Learning outcomes
Embedding similarity in practice. Precision vs recall. Calibration with a held-out split.
Why "zero false hits" on replay can lie. Two-stage decisions. Cache invalidation. Cache poisoning.

## 9. Decision gate
Fixed criteria. Expected: educational. The reel uses the honest result, whatever it is.

## 10. Risks
Savings may be small for a fast local model. That is a valid result and becomes the content.
