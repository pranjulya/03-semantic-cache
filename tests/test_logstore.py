from scache.logstore import LogStore
from scache.models import Record


def rec(q="q", decision="miss"):
    return Record(question=q, answer="a", decision=decision, similarity=None, matched_question=None,
                  latency_ms=12.5, llm_ms=10.0, model_tag="m")


def test_log_and_rows_roundtrip():
    db = LogStore(":memory:")
    db.log(rec("one"), run_id="r1", tenant="t", policy_version="v2", intent="np:x")
    db.log(rec("two", "hit"), run_id="r2")
    rows = db.rows()
    assert [r["question"] for r in rows] == ["one", "two"]
    assert rows[0]["intent"] == "np:x" and rows[0]["policy_version"] == "v2" and rows[0]["tenant"] == "t"
    assert rows[0]["latency_ms"] == 12.5 and rows[0]["similarity"] is None
    assert rows[1]["decision"] == "hit"


def test_rows_filter_by_run():
    db = LogStore(":memory:")
    db.log(rec("a"), run_id="r1")
    db.log(rec("b"), run_id="r2")
    assert [r["question"] for r in db.rows("r2")] == ["b"]


def test_file_db_creates_parent_and_persists(tmp_path):
    path = tmp_path / "sub" / "log.sqlite"
    db = LogStore(path)
    db.log(rec("x"))
    db.close()
    assert [r["question"] for r in LogStore(path).rows()] == ["x"]


def test_old_log_without_write_back_columns_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, run_id TEXT, tenant TEXT NOT NULL,"
               " policy_version TEXT NOT NULL, intent TEXT, question TEXT NOT NULL, answer TEXT NOT NULL, decision TEXT NOT NULL,"
               " similarity REAL, matched_question TEXT, latency_ms REAL NOT NULL, llm_ms REAL, model_tag TEXT NOT NULL)")
    db.commit()
    db.close()
    log = LogStore(path)
    log.log(rec("x").model_copy(update={"stored": False, "store_reason": "refusal"}))
    (row,) = log.rows()
    assert row["stored"] == 0 and row["store_reason"] == "refusal"


def test_log_from_another_thread():
    import threading
    log = LogStore(":memory:")
    rec = Record(question="q", answer="a", decision="miss", similarity=None, matched_question=None,
                 latency_ms=1.0, llm_ms=1.0, model_tag="m")
    t = threading.Thread(target=lambda: log.log(rec))
    t.start()
    t.join()
    assert len(log.rows()) == 1
