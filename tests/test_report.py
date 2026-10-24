import json

from scache.logstore import LogStore
from scache.models import Record
from scache.report import (
    build_report, cumulative_latency, comparison_rows, markdown_table, plot_lines, request_stats,
)


def row(decision, latency=10.0, llm=None, stored=None, reason=None):
    return {"decision": decision, "latency_ms": latency, "llm_ms": llm, "stored": stored, "store_reason": reason}


def write_result(d, stem, run_id, hits=0, false=0, p50=100.0, wall=20.0, llm=18.0, recall=0.5):
    (d / f"{stem}.json").write_text(json.dumps({
        "run_id": run_id, "n": 200, "latency_ms": {"p50": p50, "mean": p50 + 5}, "llm_total_s": llm,
        "wall_total_s": wall, "quality": {"hits": hits, "false_hits": false, "repeat_recall": recall}}))


def test_request_stats_empty():
    s = request_stats([])
    assert s["requests"] == 0 and s["hit_rate"] is None and s["llm_seconds_spent"] == 0
    assert s["llm_seconds_avoided_estimate"] == 0.0


def test_request_stats_counts_and_estimate():
    rows = [row("miss", 1000, 900, True, "ok"), row("miss", 1200, 1100, False, "refusal"),
            row("hit", 20), row("hit", 30), row("bypass_personal", 800, 700, False, "personal")]
    s = request_stats(rows, cache_entries=1)
    assert s["decisions"] == {"bypass_personal": 1, "hit": 2, "miss": 2}
    assert s["hit_rate"] == 0.4
    assert s["not_stored"] == {"refusal": 1, "personal": 1}
    assert s["llm_seconds_spent"] == 2.7
    assert s["llm_seconds_avoided_estimate"] == 2 * 0.9  # median of 900, 1100, 700 is 900
    assert s["latency_ms_by_decision"]["hit"]["p50"] == 25 and s["cache_entries"] == 1


def test_comparison_rows_saving_and_missing_files(tmp_path):
    write_result(tmp_path, "m1_baseline_v4_7b_30", "base", wall=100.0)
    write_result(tmp_path, "m5_replay_30_judge_high", "final", hits=44, false=1, wall=70.0)
    rows = comparison_rows(tmp_path)
    assert [r["config"] for r in rows] == ["no cache", "semantic + guard + 7B judge"]
    final = rows[1]
    assert final["wall_saved_s"] == 30.0 and final["correct_hit_rate"] == 43 / 200 and final["repeat_rate"] == 30


def test_comparison_rows_no_baseline(tmp_path):
    write_result(tmp_path, "m2_exact_10", "x", hits=6)
    assert comparison_rows(tmp_path)[0]["wall_saved_s"] is None


def test_markdown_table_formats_none(tmp_path):
    write_result(tmp_path, "m2_exact_10", "x", hits=6, recall=None)
    t = markdown_table(comparison_rows(tmp_path))
    assert t.splitlines()[0].startswith("| repeat rate") and "| 10% | exact match | 6 | 0 | - |" in t


def test_cumulative_latency():
    assert cumulative_latency([]) == []
    assert cumulative_latency([{"latency_ms": 500}, {"latency_ms": 1500}]) == [0.5, 2.0]


def test_plot_lines_writes_png(tmp_path):
    write_result(tmp_path, "m2_exact_10", "x", hits=6)
    write_result(tmp_path, "m2_exact_30", "y", hits=19)
    p = plot_lines(comparison_rows(tmp_path), "p50_ms", "ms", "t", tmp_path / "out" / "a.png")
    assert p.exists() and p.read_bytes()[:4] == b"\x89PNG"


def test_build_report_writes_files(tmp_path):
    res, out, db = tmp_path / "res", tmp_path / "out", tmp_path / "log.sqlite"
    res.mkdir()
    write_result(res, "m1_baseline_v4_7b_30", "base")
    write_result(res, "m4_sem093_30", "sem", hits=10)
    log = LogStore(db)
    for run in ("base", "sem"):
        log.log(Record(question="q", answer="a", decision="miss", similarity=None, matched_question=None,
                       latency_ms=100.0, llm_ms=90.0, model_tag="m"), run_id=run)
    log.close()
    r = build_report(res, db, out)
    names = {p.rsplit("/", 1)[1] for p in r["files"]}
    assert r["rows"] == 2 and {"hit_rate.png", "latency_p50.png", "cumulative_30.png", "report.md"} <= names
    assert "semantic 0.93 + entity guard" in (out / "report.md").read_text()


def test_build_report_without_log(tmp_path):
    res = tmp_path / "res"
    res.mkdir()
    write_result(res, "m1_baseline_v4_7b_30", "base")
    r = build_report(res, tmp_path / "missing.sqlite", tmp_path / "out")
    assert not any("cumulative" in f for f in r["files"])
