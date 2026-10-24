import json

import httpx

from scache import cli
from scache.config import Settings
from scache.llm import OllamaClient
from scache.service import Service


def fake_make_service(handler):
    def make(settings, run_id=None, log=True, mode="none", threshold=None, cache=None, guard=True, fallback=True, **kw):
        client = OllamaClient(settings, transport=httpx.MockTransport(handler))
        return Service(client=client, policy="P", settings=settings, run_id=run_id, mode=mode,
                       cache=cache, threshold=threshold, guard=guard, fallback=fallback, **kw)
    return make


def ok(req):
    if req.url.path == "/api/tags":
        return httpx.Response(200, json={"models": []})
    return httpx.Response(200, json={"response": "answer"})


def write_traffic(path, n=3):
    path.write_text("".join(json.dumps({"id": f"t{i}", "question": f"q{i}", "intent": f"i{i}"}) + "\n" for i in range(n)))


def test_ask_prints_answer(monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_service", fake_make_service(ok))
    assert cli.main(["ask", "How long?", "--no-log"]) == 0
    assert capsys.readouterr().out.strip() == "answer"


def test_replay_writes_summary(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "make_service", fake_make_service(ok))
    traffic, out = tmp_path / "t.jsonl", tmp_path / "res" / "o.json"
    write_traffic(traffic)
    assert cli.main(["replay", "--traffic", str(traffic), "--out", str(out), "--no-log", "--limit", "2"]) == 0
    res = json.loads(out.read_text())
    assert res["n"] == 2 and res["failed"] == [] and res["mode"] == "no-cache baseline"
    assert res["decisions"] == {"miss": 2}


def test_replay_exit_1_when_a_request_fails(monkeypatch, tmp_path):
    def handler(req):
        return httpx.Response(500)

    monkeypatch.setattr(cli, "make_service", fake_make_service(handler))
    traffic, out = tmp_path / "t.jsonl", tmp_path / "o.json"
    write_traffic(traffic, 2)
    assert cli.main(["replay", "--traffic", str(traffic), "--out", str(out), "--no-log"]) == 1
    assert json.loads(out.read_text())["failed"] == ["t0", "t1"]


def test_ollama_down_gives_exit_3(monkeypatch, capsys):
    def handler(req):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(cli, "make_service", fake_make_service(handler))
    assert cli.main(["ask", "q", "--no-log"]) == cli.EXIT_OLLAMA_DOWN
    assert "ollama serve" in capsys.readouterr().err


def test_model_flag_sets_llm_tag():
    args = cli.build_parser().parse_args(["ask", "q", "--model", "foo:1b"])
    assert cli._settings(args).llm_tag == "foo:1b"
    args = cli.build_parser().parse_args(["ask", "q"])
    assert cli._settings(args).llm_tag == Settings().llm_tag


def test_promptcheck_scores_and_writes(monkeypatch, tmp_path, capsys):
    def handler(req):
        return httpx.Response(200, json={"response": "30 days"})

    monkeypatch.setattr(cli, "make_service", fake_make_service(handler))
    answers, out = tmp_path / "a.jsonl", tmp_path / "o.json"
    rows = [
        {"id": "P-1", "question": "a", "answerable": True, "expect": [["30"]], "split": "dev"},
        {"id": "P-2", "question": "b", "answerable": False, "expect": [], "split": "dev"},
        {"id": "P-3", "question": "c", "answerable": True, "expect": [["99"]], "split": "test"},
    ]
    answers.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cli.main(["promptcheck", "--answers", str(answers), "--split", "dev", "--prompt", "v1", "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["n"] == 2 and res["correct_answerable"] == 1 and res["false_answer"] == 1
    assert res["prompt"] == "v1" and res["split"] == "dev"
    assert "answerable 1/1" in capsys.readouterr().out


def test_prompt_flag_sets_prompt_version():
    args = cli.build_parser().parse_args(["replay", "--traffic", "t", "--prompt", "v2"])
    assert cli._settings(args).prompt_version == "v2"


def test_replay_exact_mode_reports_quality_and_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "make_service", fake_make_service(ok))
    traffic, out, base = tmp_path / "t.jsonl", tmp_path / "o.json", tmp_path / "b.json"
    rows = [
        {"id": "t1", "question": "How long?", "intent": "i1", "repeat": False},
        {"id": "t2", "question": "How long?", "intent": "i1", "repeat": True},
        {"id": "t3", "question": "Other?", "intent": "i2", "repeat": False},
    ]
    traffic.write_text("".join(json.dumps(r) + "\n" for r in rows))
    base.write_text(json.dumps({"wall_total_s": 10.0, "llm_total_s": 9.0}))
    assert cli.main(["replay", "--traffic", str(traffic), "--mode", "exact", "--no-log",
                     "--out", str(out), "--baseline", str(base)]) == 0
    res = json.loads(out.read_text())
    assert res["decisions"] == {"miss": 2, "hit": 1}
    assert res["quality"]["hits"] == 1 and res["quality"]["false_hits"] == 0
    assert res["quality"]["repeat_recall"] == 1.0
    assert res["vs_baseline"]["wall_saved_s"] > 9.9 and res["mode"] == "exact-match cache"


def test_replay_semantic_without_threshold_is_usage_error(tmp_path, capsys):
    traffic = tmp_path / "t.jsonl"
    write_traffic(traffic, 1)
    assert cli.main(["replay", "--traffic", str(traffic), "--mode", "semantic", "--no-log"]) == 2
    assert "--threshold" in capsys.readouterr().err


def test_sweep_command_runs_and_writes(monkeypatch, tmp_path, capsys):
    from fakes import FakeEmbedder, unit_at
    import scache.embed

    vecs = {"p1": unit_at(0), "p2": unit_at(5), "d1": unit_at(0), "d2": unit_at(60)}
    monkeypatch.setattr(scache.embed, "Embedder", lambda: FakeEmbedder(vecs))
    rows = [
        {"id": "A-par-01", "type": "paraphrase", "q1": "p1", "q2": "p2", "label": "hit", "split": "calibration"},
        {"id": "A-dif-01", "type": "different", "q1": "d1", "q2": "d2", "label": "miss", "split": "calibration"},
        {"id": "A-par-02", "type": "paraphrase", "q1": "p1", "q2": "p2", "label": "hit", "split": "heldout"},
        {"id": "A-dif-02", "type": "different", "q1": "d1", "q2": "d2", "label": "miss", "split": "heldout"},
    ]
    pairs, out = tmp_path / "p.jsonl", tmp_path / "s.json"
    pairs.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cli.main(["sweep", "--pairs", str(pairs), "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["heldout"]["hits"] == 1 and res["heldout"]["false_hits"] == 0 and res["pairs"] == str(pairs)
    text = capsys.readouterr().out
    assert "chosen LOW" in text and "held-out at the chosen values" in text


def test_replay_verifier_needs_semantic_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_service", fake_make_service(ok))
    t = tmp_path / "t.jsonl"
    write_traffic(t)
    assert cli.main(["replay", "--traffic", str(t), "--mode", "exact", "--verifier", "judge", "--no-log"]) == 2
    assert "--verifier needs --mode semantic" in capsys.readouterr().err


def test_clear_empties_the_cache(tmp_path, monkeypatch, capsys):
    from fakes import FakeEmbedder, unit_at

    from scache.cache import SemanticCache
    from scache.models import Scope

    monkeypatch.setenv("SCACHE_CACHE_DIR", str(tmp_path / "c"))
    SemanticCache(FakeEmbedder({"a": unit_at(0)}), tmp_path / "c").store("a", "A", Scope(
        tenant="demo", policy_version="v1", prompt_version="v4", model_tag="m"))
    assert cli.main(["clear"]) == 0
    assert "cleared 1 cache entries" in capsys.readouterr().out
    assert SemanticCache(None, tmp_path / "c").count() == 0


def test_new_commands_are_registered():
    p = cli.build_parser()
    assert p.parse_args(["security", "--no-gates"]).no_gates is True
    assert p.parse_args(["invalidate-demo"]).policy_v2 == "data/policy_v2.md"


def test_report_command(tmp_path, capsys):
    from scache.cli import main
    assert main(["report", "--results", str(tmp_path), "--log", str(tmp_path / "none.sqlite"), "--out", str(tmp_path / "o")]) == 0
    assert "0 result rows" in capsys.readouterr().out
