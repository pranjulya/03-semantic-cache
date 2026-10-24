import argparse
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from scache.config import Settings
from scache.llm import LLMError, OllamaClient
from scache.logstore import LogStore
from scache.promptcheck import evaluate, load_checks
from scache.cache import ExactCache, SemanticCache
from scache.entity import entities_compatible
from scache.sweep import guard_report, pair_sims, render_table, run_sweep, with_guard
from scache.replay import hit_quality, intent_map, load_traffic, replay_items, summarize
from scache.service import Service
from scache.safety import choose_min_sim, run_pass, run_security, summarize_pass, topic_table
from scache.sweep import frange
from scache.verifycheck import (
    apply_threshold, choose_low_verified, choose_score_threshold, low_table, reachable, score_pairs, verifier_report,
)

EXIT_OLLAMA_DOWN = 3


def make_service(settings: Settings, run_id: str | None = None, log: bool = True, mode: str = "none",
                 threshold: float | None = None, cache=None, guard: bool = True, fallback: bool = True,
                 verifier=None, low: float | None = None, verify_high: bool = False) -> Service:
    store = LogStore(settings.log_db) if log else None
    return Service(client=OllamaClient(settings), settings=settings, log=store, run_id=run_id,
                   mode=mode, cache=cache, threshold=threshold, guard=guard, fallback=fallback,
                   verifier=verifier, low=low, verify_high=verify_high)


def _settings(args) -> Settings:
    overrides = {}
    if args.model:
        overrides["llm_tag"] = args.model
    if args.prompt:
        overrides["prompt_version"] = args.prompt
    return Settings(**overrides)


def cmd_ask(args) -> int:
    svc = make_service(_settings(args), run_id="cli-ask", log=not args.no_log)
    rec = svc.ask(args.question)
    print(rec.answer)
    print(f"[{rec.decision}] {rec.latency_ms:.0f} ms (llm {rec.llm_ms:.0f} ms, {rec.model_tag})", file=sys.stderr)
    return 0


def cmd_replay(args) -> int:
    settings = _settings(args)
    if args.mode == "semantic" and args.threshold is None:
        print("error: --mode semantic needs --threshold", file=sys.stderr)
        return 2
    if args.verifier and args.mode != "semantic":
        print("error: --verifier needs --mode semantic", file=sys.stderr)
        return 2
    traffic = load_traffic(args.traffic)[: args.limit]
    tag = args.mode + (f"-{args.threshold}{'-noguard' if args.no_guard else ''}{'-nofallback' if args.no_fallback else ''}{'-verify-' + args.verifier if args.verifier else ''}{'-high' if args.verify_high else ''}"
                       if args.mode == "semantic" else "")
    run_id = args.run_id or f"{Path(args.traffic).stem}-{tag}-{settings.llm_tag}-{int(time.time())}"
    cache_dir = None
    embedder_id = None
    if args.mode == "semantic":
        from scache.embed import Embedder
        Path(settings.cache_dir).mkdir(parents=True, exist_ok=True)
        cache_dir = tempfile.mkdtemp(prefix="replay-", dir=settings.cache_dir)  # fresh cache per run
        cache = SemanticCache(Embedder(), cache_dir)
        embedder_id = cache.embedder.model_id
    else:
        cache = ExactCache() if args.mode == "exact" else None
    verifier, low = None, None
    if args.verifier:
        from scache.verifier import CrossEncoderVerifier, LLMJudgeVerifier
        low = args.low if args.low is not None else settings.low
        verifier = LLMJudgeVerifier(OllamaClient(settings)) if args.verifier == "judge" else CrossEncoderVerifier()
    svc = make_service(settings, run_id=run_id, log=not args.no_log, mode=args.mode,
                       threshold=args.threshold, cache=cache, guard=not args.no_guard, fallback=not args.no_fallback,
                       verifier=verifier, low=low, verify_high=args.verify_high)
    failed: list[str] = []
    t0 = time.perf_counter()
    try:
        pairs = replay_items(
            traffic, svc.ask,
            on_error=lambda item, e: (failed.append(item["id"]), print(f"error {item['id']}: {e}", file=sys.stderr)),
            on_record=lambda i, r: print(f"{i}/{len(traffic)} {r.decision} {r.latency_ms:.0f} ms", file=sys.stderr) if i % 20 == 0 else None,
        )
    finally:
        if cache_dir:
            shutil.rmtree(cache_dir, ignore_errors=True)
    out = {
        "run_id": run_id,
        "traffic": str(args.traffic),
        "mode": {"none": "no-cache baseline", "exact": "exact-match cache", "semantic": "single-threshold semantic cache"}[args.mode],
        "threshold": args.threshold,
        "guard": args.mode == "semantic" and not args.no_guard,
        "fallback": args.mode == "semantic" and not args.no_fallback,
        "verifier": verifier.name if verifier else None,
        "low": low,
        "verify_high": args.verify_high,
        "embedder": embedder_id,
        "model": settings.llm_tag,
        "prompt": settings.prompt_version,
        "model_digest": svc.client.model_digest(),
        "policy_version": settings.policy_version,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": time.perf_counter() - t0,
        "failed": failed,
        **summarize([r for _, r in pairs]),
        "quality": hit_quality(pairs, intent_map(traffic)),
    }
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())
        out["vs_baseline"] = {
            "baseline": str(args.baseline),
            "wall_saved_s": base["wall_total_s"] - out["wall_total_s"],
            "llm_saved_s": base["llm_total_s"] - out["llm_total_s"],
            "note": "separate runs; LLM time varies a little between runs",
        }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=2))
    q = out["quality"]
    print(json.dumps({
        "run_id": run_id, "n": out["n"], "latency_ms": out["latency_ms"], "llm_total_s": out["llm_total_s"],
        "hits": q["hits"], "false_hits": q["false_hits"], "repeat_recall": q["repeat_recall"], "failed": failed,
    }, indent=2))
    return 1 if failed else 0


def cmd_promptcheck(args) -> int:
    settings = _settings(args)
    rows = load_checks(args.answers, args.split)
    svc = make_service(settings, log=False)
    res = evaluate(rows, lambda q: svc.ask(q).answer)
    res.update(model=settings.llm_tag, prompt=settings.prompt_version, split=args.split or "all")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, indent=2))
    print(f"{res['model']} prompt {res['prompt']} split {res['split']}: "
          f"answerable {res['correct_answerable']}/{res['n_answerable']} "
          f"(wrong refusal {res['wrong_refusal']}, wrong answer {res['wrong_answer']}); "
          f"unanswerable {res['correct_unanswerable']}/{res['n_unanswerable']}")
    if args.verbose:
        for x in res["results"]:
            if x["outcome"] != "ok":
                print(f"  {x['outcome']:13} {x['question']} -> {x['answer']!r}")
    return 0


def cmd_sweep(args) -> int:
    from scache.embed import Embedder
    pairs = [json.loads(line) for line in Path(args.pairs).read_text().splitlines() if line.strip()]
    scored = pair_sims(pairs, Embedder())
    if not args.no_guard:
        scored = with_guard(scored, entities_compatible)
    res = run_sweep(scored, args.max_false_rate, args.low_quantile)
    res["pairs"] = args.pairs
    res["guard"] = None if args.no_guard else guard_report(scored)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, indent=2))
    c = res["chosen"]
    if res["guard"]:
        g = res["guard"]
        print("entity guard rejects: " + ", ".join(f"{t} {v['rejected']}/{v['total']}" for t, v in g["by_type"].items())
              + f"; paraphrases wrongly rejected: {g['paraphrases_rejected']}")
    print(f"chosen LOW {c['low']:.3f}  HIGH {c['high']:.3f}  (rule: most hits, 0 near-miss false hits, "
          f"false-hit rate <= {res['rule']['max_false_rate']:.0%}, on the calibration split)")
    for split in ("calibration", "heldout"):
        print(f"\n{split} split, LOW fixed at {c['low']:.3f}:")
        print(render_table(res["table"][split], c["high"]))
    h, (lo, hi) = res["heldout"], res["heldout_false_hit_ci95"]
    print(f"\nheld-out at the chosen values: hits {h['hits']}/{h['n_paraphrase']}, false hits {h['false_hits']}/{h['n_miss']} "
          f"(95% CI {lo:.1%}-{hi:.1%}), near-miss false hits {h['near_miss_false_hits']}")
    return 0


def cmd_verifiers(args) -> int:
    from scache.embed import Embedder
    from scache.verifier import CROSS_ENCODER_ID, CrossEncoderVerifier, LLMJudgeVerifier
    s = Settings(**({"llm_tag": args.model} if args.model else {}))
    pairs = [json.loads(line) for line in Path(args.pairs).read_text().splitlines() if line.strip()]
    pairs = with_guard(pair_sims(pairs, Embedder()), entities_compatible)
    low, high = (args.low if args.low is not None else s.low), (args.high if args.high is not None else s.high)
    if args.kind == "cross":
        verifier = CrossEncoderVerifier(model_id=args.ce_model or CROSS_ENCODER_ID)
    else:
        verifier = LLMJudgeVerifier(OllamaClient(s))
    scored = score_pairs(pairs, verifier)
    threshold = None
    if args.kind == "cross":
        cal = [p for p in scored if p["split"] == "calibration"]
        threshold = choose_score_threshold(cal, low)
        scored = apply_threshold(scored, threshold)
    res = {"verifier": verifier.name, "low": low, "high": high, "score_threshold": threshold, "pairs": args.pairs,
           "model_digest": OllamaClient(s).model_digest() if args.kind == "judge" else None}
    for split in ("calibration", "heldout"):
        res[split] = verifier_report([p for p in scored if p["split"] == split], low, high)
    lows = frange(0.60, 0.90, 0.01)
    res["low_table"] = {sp: low_table([p for p in scored if p["split"] == sp], high, lows) for sp in ("calibration", "heldout")}
    res["chosen_low"] = choose_low_verified(res["low_table"]["calibration"])
    ch = {sp: next(r for r in res["low_table"][sp] if abs(r["low"] - res["chosen_low"]) < 1e-9) for sp in ("calibration", "heldout")}
    res["scored"] = [{k: p[k] for k in ("id", "type", "label", "split", "sim", "guard_ok", "v_same", "v_score", "v_ms")}
                     for p in scored]
    res["misses"] = [{k: p[k] for k in ("id", "type", "q1", "q2", "sim", "v_score")}
                     for p in reachable(scored, low) if p["v_same"] != (p["label"] == "hit")]
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, indent=2))
    print(f"{verifier.name}  LOW {low}  HIGH {high}" + (f"  score threshold {threshold:.4f} (calibration split)" if threshold is not None else ""))
    for split in ("calibration", "heldout"):
        for view in ("all", "reachable", "gray", "above_high"):
            r = res[split][view]
            print(f"  {split:<11} {view:<10} paraphrase {r['paraphrase_accepted']:>2}/{r['n_paraphrase']:<2} accepted, "
                  f"miss {r['miss_accepted']:>2}/{r['n_miss']:<2} accepted (near-miss {r['near_miss_accepted']}/{r['n_near_miss']}), "
                  f"acc {r['accuracy']:.2f}, p50 {r['ms_p50']:.0f} ms p95 {r['ms_p95']:.0f} ms")
    print(f"  chosen LOW {res['chosen_low']:.2f} (most verified hits, zero verified false accepts, calibration split; then highest LOW)")
    for sp in ("calibration", "heldout"):
        r = ch[sp]
        print(f"  {sp:<11} at LOW {r['low']:.2f}: direct hits {r['hits_direct']}, verified hits {r['hits_verified']} of "
              f"{r['n_paraphrase']} paraphrases, false {r['false_direct'] + r['false_verified']}, verifier calls {r['verifier_calls']}")
    for m in res["misses"]:
        print(f"  wrong: {m['id']} {m['type']} sim {m['sim']:.3f} score {m['v_score']:.3f} | {m['q1']} | {m['q2']}")
    return 0


def _fresh_semantic_service(settings: Settings, policy: str, cache, **kw) -> Service:
    return Service(client=OllamaClient(settings), settings=settings, policy=policy, mode="semantic", cache=cache,
                   threshold=settings.high, **kw)


def cmd_invalidate_demo(args) -> int:
    """Set C: v1 answers cached, then the policy changes to v2. Four ways to deal with it (see safety.py)."""
    from scache.embed import Embedder
    base = _settings(args)
    rows = [json.loads(line) for line in Path(args.staleness).read_text().splitlines() if line.strip()]
    v1_text, v2_text = Path(base.policy_path).read_text(), Path(args.policy_v2).read_text()
    v1s, v2s = base.model_copy(update={"policy_version": "v1"}), base.model_copy(update={"policy_version": "v2"})
    embedder = Embedder()
    Path(base.cache_dir).mkdir(parents=True, exist_ok=True)
    out: dict = {"model": base.llm_tag, "prompt": base.prompt_version, "policy_v1": str(base.policy_path),
                 "policy_v2": args.policy_v2, "ttl_s": base.ttl_s, "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "scenarios": {}}

    def filled():
        d = tempfile.mkdtemp(prefix="stale-", dir=base.cache_dir)
        cache = SemanticCache(embedder, d)
        first = run_pass(_fresh_semantic_service(v1s, v1_text, cache), rows)
        return cache, d, first

    def finish(name, results, **extra):
        out["scenarios"][name] = {"summary": summarize_pass(results), "results": results, **extra}
        s = out["scenarios"][name]["summary"]
        print(f"{name:<6} changed: stale {s['changed']['stale']}, fresh {s['changed']['fresh']}, unclear {s['changed']['unclear']}, "
              f"served from cache {s['changed']['hits']} of {s['changed']['n']}; controls served from cache {s['controls']['hits']} of {s['controls']['n']}"
              + "".join(f"; {k} {v}" for k, v in extra.items() if not isinstance(v, (list, dict))))

    dirs = []
    try:
        cache, d, first = filled()
        dirs.append(d)
        out["first_pass_v1"] = summarize_pass(first)
        print("v1 answers cached:", out["first_pass_v1"]["changed"], out["first_pass_v1"]["controls"])
        finish("none", run_pass(_fresh_semantic_service(v1s, v2_text, cache), rows))  # policy text is v2, label still v1

        cache, d, _ = filled()
        dirs.append(d)
        finish("bump", run_pass(_fresh_semantic_service(v2s, v2_text, cache), rows))

        cache, d, _ = filled()
        dirs.append(d)
        sims = frange(0.40, 0.95, 0.05)
        table = topic_table(cache, rows, sims)
        min_sim = choose_min_sim(table)
        deleted = sum(cache.invalidate_similar(r["topic"], min_sim) for r in {r["fact"]: r for r in rows if r["changed"]}.values())
        out["topic_table"], out["min_sim"] = table, min_sim
        for t in table:
            print(f"  min_sim {t['min_sim']:.2f}: covers {t['covered']}/{t['targets']} changed-fact entries, deletes {t['controls_deleted']}/{t['controls']} controls")
        finish("topic", run_pass(_fresh_semantic_service(v1s, v2_text, cache), rows), min_sim=min_sim, deleted=deleted)

        cache, d, _ = filled()
        dirs.append(d)
        finish("ttl", run_pass(_fresh_semantic_service(v1s, v2_text, cache, clock=lambda: time.time() + base.ttl_s + 1), rows),
               ttl_s=base.ttl_s)
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=2))
    return 0


def cmd_security(args) -> int:
    from scache.embed import Embedder
    base = _settings(args)
    rows = [json.loads(line) for line in Path(args.security).read_text().splitlines() if line.strip()]
    Path(base.cache_dir).mkdir(parents=True, exist_ok=True)
    d = tempfile.mkdtemp(prefix="sec-", dir=base.cache_dir)
    try:
        cache = SemanticCache(Embedder(), d)
        svc = _fresh_semantic_service(base, Path(base.policy_path).read_text(), cache, gates=not args.no_gates)
        res = run_security(svc, cache, rows)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    out = {"gates": not args.no_gates, "model": base.llm_tag, "prompt": base.prompt_version, "results": res,
           "passed": sum(r["ok"] for r in res), "n": len(res)}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=2))
    for r in res:
        extra = (f"second {r['second_decision']}" if r["kind"] == "personal" else
                 f"complied {r['complied']}, clean question -> {r['clean_decision']}, matched poison {r['clean_matched_poison']}")
        print(f"{r['id']} {r['kind']:<8} {'ok  ' if r['ok'] else 'FAIL'} {r['decision']:<16} stored {r['stored']} ({r['store_reason']}); {extra}")
        if args.verbose:
            print(f"      answer: {r['answer'][:160]!r}")
    print(f"gates {'on' if not args.no_gates else 'OFF'}: {out['passed']}/{out['n']} passed")
    return 0 if out["passed"] == out["n"] else 1


def cmd_report(args) -> int:
    from scache.report import build_report
    res = build_report(args.results, args.log, args.out, cumulative_rate=args.cumulative_rate)
    print(f"{res['rows']} result rows; wrote:")
    for f in res["files"]:
        print(f"  {f}")
    return 0


def cmd_demo(args) -> int:
    from scache.demo import run_demo
    from scache.embed import Embedder
    from scache.verifier import LLMJudgeVerifier
    settings = _settings(args)
    Path(settings.cache_dir).mkdir(parents=True, exist_ok=True)
    cache_dir = tempfile.mkdtemp(prefix="demo-", dir=settings.cache_dir)  # throwaway cache
    try:
        client = OllamaClient(settings)
        svc = Service(client=client, settings=settings, mode="semantic", cache=SemanticCache(Embedder(), cache_dir),
                      threshold=settings.high, low=settings.low, verify_high=settings.verify_high,
                      verifier=LLMJudgeVerifier(client))
        results = run_demo(svc)
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)
    return 0 if all(r["as_expected"] for r in results) else 1


def cmd_clear(args) -> int:
    cache = SemanticCache(None, Settings().cache_dir)
    n = cache.count()
    cache.clear()
    print(f"cleared {n} cache entries")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scache")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="answer one question (M1: always the LLM)")
    a.add_argument("question")
    a.add_argument("--model")
    a.add_argument("--prompt", help="prompt version (scache.llm.PROMPTS)")
    a.add_argument("--no-log", action="store_true")
    a.set_defaults(fn=cmd_ask)

    r = sub.add_parser("replay", help="replay a traffic file through no cache, an exact-match cache or a semantic cache")
    r.add_argument("--traffic", required=True)
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--model")
    r.add_argument("--prompt", help="prompt version (scache.llm.PROMPTS)")
    r.add_argument("--mode", choices=["none", "exact", "semantic"], default="none")
    r.add_argument("--threshold", type=float, help="similarity threshold for --mode semantic")
    r.add_argument("--no-guard", action="store_true", help="semantic mode: skip the entity guard (M2 behaviour)")
    r.add_argument("--no-fallback", action="store_true", help="semantic mode: only try the best candidate")
    r.add_argument("--verifier", choices=["judge", "cross"], help="semantic mode: verify gray-zone candidates (LOW..threshold)")
    r.add_argument("--low", type=float, help="gray-zone floor for --verifier (default: settings.low)")
    r.add_argument("--verify-high", action="store_true", help="also verify candidates at or above the threshold")
    r.add_argument("--baseline", help="baseline summary JSON; adds seconds saved vs that run")
    r.add_argument("--run-id")
    r.add_argument("--out", help="write the summary JSON here")
    r.add_argument("--no-log", action="store_true")
    r.set_defaults(fn=cmd_replay)

    c = sub.add_parser("promptcheck", help="score a prompt version on eval/answers.jsonl")
    c.add_argument("--answers", default="eval/answers.jsonl")
    c.add_argument("--split", choices=["dev", "test"])
    c.add_argument("--model")
    c.add_argument("--prompt")
    c.add_argument("--out")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(fn=cmd_promptcheck)

    w = sub.add_parser("sweep", help="calibrate LOW/HIGH on the Set A calibration split, report held-out")
    w.add_argument("--pairs", default="eval/pairs.jsonl")
    w.add_argument("--max-false-rate", type=float, default=0.01)
    w.add_argument("--low-quantile", type=float, default=0.05)
    w.add_argument("--no-guard", action="store_true", help="ignore the entity guard (reproduces M3)")
    w.add_argument("--out")
    w.set_defaults(fn=cmd_sweep)

    v = sub.add_parser("verifiers", help="score a gray-zone verifier on Set A (accuracy, ms)")
    v.add_argument("--kind", choices=["cross", "judge"], required=True)
    v.add_argument("--pairs", default="eval/pairs.jsonl")
    v.add_argument("--model", help="Ollama tag for --kind judge")
    v.add_argument("--ce-model", help="cross-encoder model id for --kind cross")
    v.add_argument("--low", type=float)
    v.add_argument("--high", type=float)
    v.add_argument("--out")
    v.set_defaults(fn=cmd_verifiers)

    d = sub.add_parser("invalidate-demo", help="Set C: stale answers after a policy change, four ways to handle it")
    d.add_argument("--staleness", default="eval/staleness.jsonl")
    d.add_argument("--policy-v2", default="data/policy_v2.md")
    d.add_argument("--model")
    d.add_argument("--prompt")
    d.add_argument("--out")
    d.set_defaults(fn=cmd_invalidate_demo)

    sc = sub.add_parser("security", help="personal-question bypass and poisoning checks")
    sc.add_argument("--security", default="eval/security.jsonl")
    sc.add_argument("--no-gates", action="store_true", help="reproduce M2-M5: no personal bypass, minimal write-back")
    sc.add_argument("--model")
    sc.add_argument("--prompt")
    sc.add_argument("--out")
    sc.add_argument("-v", "--verbose", action="store_true")
    sc.set_defaults(fn=cmd_security)

    rp = sub.add_parser("report", help="tables and charts from saved replay results")
    rp.add_argument("--results", default="eval/results")
    rp.add_argument("--log", default="logs/requests.sqlite")
    rp.add_argument("--out", default="report")
    rp.add_argument("--cumulative-rate", type=int, default=30)
    rp.set_defaults(fn=cmd_report)

    dm = sub.add_parser("demo", help="scripted walk through hit, paraphrase, near-miss, personal bypass, policy change")
    dm.add_argument("--model")
    dm.add_argument("--prompt")
    dm.set_defaults(fn=cmd_demo)

    k = sub.add_parser("clear", help="delete every cache entry")
    k.set_defaults(fn=cmd_clear)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except LLMError as e:
        print(f"error: LLM unavailable ({e}). Start Ollama with: ollama serve", file=sys.stderr)
        return EXIT_OLLAMA_DOWN


if __name__ == "__main__":
    sys.exit(main())
