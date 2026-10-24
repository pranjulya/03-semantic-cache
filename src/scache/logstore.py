import sqlite3
import time
from pathlib import Path

from scache.models import Record

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT,
    tenant TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    intent TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    decision TEXT NOT NULL,
    similarity REAL,
    matched_question TEXT,
    latency_ms REAL NOT NULL,
    llm_ms REAL,
    model_tag TEXT NOT NULL,
    stored INTEGER,
    store_reason TEXT
)
"""


class LogStore:
    """SQLite request log. One row per request; `intent` is an evaluation tag, never used for decisions."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)  # the API serves from a thread pool; calls are serialised by its lock
        self.db.row_factory = sqlite3.Row
        self.db.execute(SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(requests)")}
        for col, typ in (("stored", "INTEGER"), ("store_reason", "TEXT")):  # logs written before M6
            if col not in have:
                self.db.execute(f"ALTER TABLE requests ADD COLUMN {col} {typ}")
        self.db.commit()

    def log(self, rec: Record, *, run_id: str | None = None, tenant: str = "demo",
            policy_version: str = "v1", intent: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO requests (ts, run_id, tenant, policy_version, intent, question, answer, decision,"
            " similarity, matched_question, latency_ms, llm_ms, model_tag, stored, store_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), run_id, tenant, policy_version, intent, rec.question, rec.answer, rec.decision,
             rec.similarity, rec.matched_question, rec.latency_ms, rec.llm_ms, rec.model_tag,
             None if rec.stored is None else int(rec.stored), rec.store_reason),
        )
        self.db.commit()

    def rows(self, run_id: str | None = None) -> list[dict]:
        if run_id is None:
            cur = self.db.execute("SELECT * FROM requests ORDER BY id")
        else:
            cur = self.db.execute("SELECT * FROM requests WHERE run_id = ? ORDER BY id", (run_id,))
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.db.close()
