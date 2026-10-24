"""Tables and charts from saved replay results (`eval/results/`) and the request log. matplotlib is imported lazily and
headless, so `request_stats` (used by the API) stays cheap."""
import json
from pathlib import Path

import numpy as np

from scache.logstore import LogStore

REPEAT_RATES = (10, 30, 60)

# label -> result file stem. {r} is the repeat rate. Every file is a 200-request replay of the same generated traffic
# with qwen2.5:7b and prompt v4, so the configurations are comparable (one run each).
CONFIGS = {
    "no cache": "m1_baseline_v4_7b_{r}",
    "exact match": "m2_exact_{r}",
    "semantic 0.93 + entity guard": "m4_sem093_{r}",
    "semantic + guard + 7B judge": "m5_replay_{r}_judge_high",
}
NAIVE = {"semantic 0.81, no guard (naive)": "m2_sem081_{r}"}  # the cautionary one: most hits are false
FINAL = "semantic + guard + 7B judge"


def _pct(xs: list[float], q: float) -> float | None:
    return float(np.percentile(xs, q)) if xs else None


def request_stats(rows: list[dict], cache_entries: int | None = None) -> dict:
    """Summary of logged requests (rows from `LogStore.rows`). Savings are latency and LLM seconds, never money."""
    n = len(rows)
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["decision"], []).append(r)
    decisions = {d: len(v) for d, v in sorted(by.items())}
    hits = len(by.get("hit", []))
    llm_ms = [r["llm_ms"] for r in rows if r["llm_ms"] is not None]
    not_stored: dict[str, int] = {}
    for r in rows:
        if r["stored"] == 0:
            not_stored[r["store_reason"] or "unknown"] = not_stored.get(r["store_reason"] or "unknown", 0) + 1
    miss_llm = [r["llm_ms"] for r in rows if r["decision"] != "hit" and r["llm_ms"] is not None]
    return {
        "requests": n,
        "decisions": decisions,
        "hit_rate": hits / n if n else None,
        "latency_ms_by_decision": {d: {"n": len(v), "p50": _pct([r["latency_ms"] for r in v], 50),
                                       "p95": _pct([r["latency_ms"] for r in v], 95)} for d, v in by.items()},
        "llm_seconds_spent": sum(llm_ms) / 1000,
        # estimate: each hit would have cost a typical (median) LLM call. Not measured.
        "llm_seconds_avoided_estimate": hits * _pct(miss_llm, 50) / 1000 if hits and miss_llm else 0.0,
        "not_stored": not_stored,
        "cache_entries": cache_entries,
    }


def load_result(results_dir: Path | str, stem: str) -> dict | None:
    path = Path(results_dir) / f"{stem}.json"
    return json.loads(path.read_text()) if path.exists() else None


def comparison_rows(results_dir: Path | str, configs: dict[str, str] | None = None) -> list[dict]:
    """One row per (configuration, repeat rate) that has a saved result, with the saving against the no-cache run."""
    configs = configs or {**CONFIGS, **NAIVE}
    rows = []
    for rate in REPEAT_RATES:
        base = load_result(results_dir, CONFIGS["no cache"].format(r=rate))
        for label, stem in configs.items():
            res = load_result(results_dir, stem.format(r=rate))
            if res is None:
                continue
            q = res.get("quality") or {}
            rows.append({
                "config": label, "repeat_rate": rate, "run_id": res["run_id"], "n": res["n"],
                "hits": q.get("hits", 0), "false_hits": q.get("false_hits", 0),
                "correct_hit_rate": (q.get("hits", 0) - q.get("false_hits", 0)) / res["n"],
                "repeat_recall": q.get("repeat_recall"),
                "p50_ms": res["latency_ms"]["p50"], "mean_ms": res["latency_ms"]["mean"],
                "llm_total_s": res["llm_total_s"], "wall_total_s": res["wall_total_s"],
                "wall_saved_s": base["wall_total_s"] - res["wall_total_s"] if base else None,
            })
    return rows


def markdown_table(rows: list[dict]) -> str:
    head = ("| repeat rate | configuration | hits | false hits | repeat recall | p50 ms | mean ms | LLM s | wall s |"
            " wall s vs no cache |\n|---|---|---|---|---|---|---|---|---|---|")
    fmt = lambda v, spec: "-" if v is None else format(v, spec)  # noqa: E731
    lines = [head]
    for r in rows:
        lines.append(
            f"| {r['repeat_rate']}% | {r['config']} | {r['hits']} | {r['false_hits']} | {fmt(r['repeat_recall'], '.2f')}"
            f" | {r['p50_ms']:.0f} | {r['mean_ms']:.0f} | {r['llm_total_s']:.0f} | {r['wall_total_s']:.0f}"
            f" | {fmt(r['wall_saved_s'], '+.0f')} |")
    return "\n".join(lines)


def _pyplot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _series(rows: list[dict], key: str) -> dict[str, tuple[list[int], list[float]]]:
    out: dict[str, tuple[list[int], list[float]]] = {}
    for r in rows:
        xs, ys = out.setdefault(r["config"], ([], []))
        xs.append(r["repeat_rate"])
        ys.append(r[key])
    return out


NOTE = "synthetic traffic, 200 requests per point, one run each, qwen2.5:7b"


def plot_lines(rows: list[dict], key: str, ylabel: str, title: str, path: Path | str) -> Path:
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for label, (xs, ys) in _series(rows, key).items():
        ax.plot(xs, ys, marker="o", label=label, linestyle="--" if label in NAIVE else "-")
    ax.set_xlabel("repeat rate of the traffic (%)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(REPEAT_RATES)
    ax.grid(alpha=0.3)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8)
    fig.text(0.01, 0.01, NOTE, fontsize=7, color="gray")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return Path(path)


def cumulative_latency(log_rows: list[dict]) -> list[float]:
    """Running total of request latency in seconds, in request order."""
    return list(np.cumsum([r["latency_ms"] for r in log_rows]) / 1000) if log_rows else []


def plot_cumulative(rows: list[dict], log: LogStore, rate: int, path: Path | str) -> Path | None:
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(7, 4.2))
    drawn = 0
    for r in rows:
        if r["repeat_rate"] != rate:
            continue
        series = cumulative_latency(log.rows(r["run_id"]))
        if series:
            ax.plot(range(1, len(series) + 1), series, label=r["config"], linestyle="--" if r["config"] in NAIVE else "-")
            drawn += 1
    if not drawn:
        plt.close(fig)
        return None
    ax.set_xlabel("request number")
    ax.set_ylabel("cumulative latency (s)")
    ax.set_title(f"Total waiting time, {rate}% repeat traffic")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.text(0.01, 0.01, NOTE, fontsize=7, color="gray")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return Path(path)


def build_report(results_dir: Path | str, log_db: Path | str, out_dir: Path | str, cumulative_rate: int = 30) -> dict:
    """Write `report.md` and the charts into `out_dir`. Charts that need data that is missing are skipped."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = comparison_rows(results_dir)
    files = [
        plot_lines(rows, "correct_hit_rate", "correct hits / requests", "Correct hit rate vs repeat rate", out / "hit_rate.png"),
        plot_lines(rows, "p50_ms", "median latency (ms)", "Median latency vs repeat rate", out / "latency_p50.png"),
        plot_lines(rows, "mean_ms", "mean latency (ms)", "Mean latency vs repeat rate", out / "latency_mean.png"),
    ]
    if Path(log_db).exists():
        log = LogStore(log_db)
        try:
            cum = plot_cumulative(rows, log, cumulative_rate, out / f"cumulative_{cumulative_rate}.png")
        finally:
            log.close()
        if cum:
            files.append(cum)
    md = [
        "# Results", "",
        f"Generated from `{results_dir}`. {NOTE}. Latency and LLM seconds, not money.", "",
        markdown_table(rows), "",
        "Read the naive row with its false-hits column: its low latency comes mostly from serving wrong answers. "
        "A negative last column means the configuration was slower than no cache (the judge call costs time on every "
        "candidate; at 10% repeats it does not pay back).", "",
        *[f"![{f.stem}]({f.name})" for f in files],
    ]
    (out / "report.md").write_text("\n".join(md) + "\n")
    return {"rows": len(rows), "files": [str(f) for f in files] + [str(out / "report.md")]}
