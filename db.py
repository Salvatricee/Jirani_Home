"""SQLite persistence. Satisfies P05's logging rule: draft version, sources, reviewer, final action."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    app_version TEXT,
    mode TEXT,
    model TEXT,
    route TEXT,
    destination TEXT,
    policy_ids TEXT,           -- JSON list of policies the draft cites
    draft_text TEXT,
    reviewer_note TEXT,
    analysis_json TEXT,
    issues_json TEXT,          -- validator result at generation time
    latency_s REAL,
    usage TEXT,
    error TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id),
    reviewer TEXT NOT NULL,
    action TEXT NOT NULL,      -- approved | edited_and_approved | rejected
    final_text TEXT,
    reason TEXT,
    issues_json TEXT,          -- validator result on the final text
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id),
    ticket_id TEXT,
    status TEXT,               -- ok | rejected | failed | simulated
    http_status INTEGER,
    payload_json TEXT,
    response_json TEXT,
    error TEXT,
    latency_s REAL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS test_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id),
    ticket_id TEXT,
    body TEXT,
    created_at TEXT
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def save_draft(ticket_id, analysis, d, issues, app_version) -> int:
    with conn() as c:
        version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM drafts WHERE ticket_id=?", (ticket_id,)).fetchone()[0]
        cur = c.execute(
            "INSERT INTO drafts (ticket_id,version,app_version,mode,model,route,destination,policy_ids,draft_text,"
            "reviewer_note,analysis_json,issues_json,latency_s,usage,error,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticket_id, version, app_version, d["mode"], d["model"], analysis.route, analysis.destination,
             json.dumps(d["policy_ids"]), d["draft"], d.get("reviewer_note", ""), json.dumps(analysis.to_dict()),
             json.dumps(issues), d.get("latency_s"), d.get("usage", ""), d.get("error", ""), now()))
        return cur.lastrowid


def latest_draft(ticket_id):
    with conn() as c:
        r = c.execute("SELECT * FROM drafts WHERE ticket_id=? ORDER BY id DESC LIMIT 1", (ticket_id,)).fetchone()
        return dict(r) if r else None


def save_review(draft_id, reviewer, action, final_text, reason, issues) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO reviews (draft_id,reviewer,action,final_text,reason,issues_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (draft_id, reviewer, action, final_text, reason, json.dumps(issues), now()))
        return cur.lastrowid


def latest_review(draft_id):
    with conn() as c:
        r = c.execute("SELECT * FROM reviews WHERE draft_id=? ORDER BY id DESC LIMIT 1", (draft_id,)).fetchone()
        return dict(r) if r else None


def add_inbox(draft_id, ticket_id, body):
    with conn() as c:
        c.execute("INSERT INTO test_inbox (draft_id,ticket_id,body,created_at) VALUES (?,?,?,?)",
                  (draft_id, ticket_id, body, now()))


def save_event(draft_id, ticket_id, payload, result):
    with conn() as c:
        c.execute(
            "INSERT INTO workflow_events (draft_id,ticket_id,status,http_status,payload_json,response_json,error,latency_s,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (draft_id, ticket_id, result["status"], result.get("http_status"), json.dumps(payload),
             json.dumps(result.get("response")), result.get("error", ""), result.get("latency_s"), now()))


def latest_event(draft_id):
    with conn() as c:
        r = c.execute("SELECT * FROM workflow_events WHERE draft_id=? ORDER BY id DESC LIMIT 1", (draft_id,)).fetchone()
        return dict(r) if r else None


def table(name):
    """Return all rows of a table as list of dicts (for the Records tab)."""
    assert name in {"drafts", "reviews", "workflow_events", "test_inbox"}
    with conn() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {name} ORDER BY id DESC").fetchall()]
