"""SQLite state store. Owner: Arth (P1). The ONLY place with SQL.

Every function opens its own short connection (safe across threads and processes).
WAL mode + busy timeout so the web server, the CLI and background threads can share the file.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from crd.models import (
    Action, Assessment, Counterparty, Evidence, LedgerEntry, ReviewRequest,
)


def db_path() -> str:
    return os.getenv("CRD_DB_PATH", "var/crd.db")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@contextmanager
def conn():
    path = db_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(path, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    try:
        yield c
    finally:
        c.close()


@contextmanager
def tx():
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, request_json TEXT, status TEXT,
  started_at TEXT, finished_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS counterparties(cp_id TEXT PRIMARY KEY, json TEXT, record_hash TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS steps(run_id TEXT, cp_id TEXT, step TEXT, status TEXT, output_json TEXT,
  updated_at TEXT, PRIMARY KEY(run_id, cp_id, step));
CREATE TABLE IF NOT EXISTS evidence(evidence_id TEXT PRIMARY KEY, run_id TEXT, cp_id TEXT, signal TEXT, json TEXT);
CREATE INDEX IF NOT EXISTS ev_run_cp ON evidence(run_id, cp_id);
CREATE TABLE IF NOT EXISTS assessments(run_id TEXT, cp_id TEXT, rating TEXT, score INT, json TEXT,
  created_at TEXT, PRIMARY KEY(run_id, cp_id));
CREATE TABLE IF NOT EXISTS actions(action_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, run_id TEXT,
  cp_id TEXT, type TEXT, status TEXT, json TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS ledger(call_id TEXT PRIMARY KEY, run_id TEXT, provider TEXT, endpoint TEXT,
  request_hash TEXT, credits REAL, inr REAL, tokens_in INT, tokens_out INT, latency_ms INT,
  status TEXT, note TEXT, ts TEXT);
CREATE INDEX IF NOT EXISTS ledger_run ON ledger(run_id);
CREATE TABLE IF NOT EXISTS raw_cache(request_hash TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS outbox(outbox_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, channel TEXT,
  to_addr TEXT, subject TEXT, body TEXT, sent_at TEXT);
CREATE TABLE IF NOT EXISTS erp_holds(hold_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, cp_id TEXT,
  reason TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS watch_events(event_id TEXT PRIMARY KEY, watch_id TEXT, payload_json TEXT,
  received_at TEXT, handled INT DEFAULT 0);
"""


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


# ---------------------------------------------------------------- runs
def new_run(req: ReviewRequest, run_id: Optional[str] = None) -> str:
    rid = run_id or _id("run")
    with tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO runs(run_id, request_json, status, started_at) VALUES(?,?,?,?)",
            (rid, req.model_dump_json(), "running", now()),
        )
    return rid


def set_run_status(run_id: str, status: str, note: str | None = None) -> None:
    with tx() as c:
        c.execute("UPDATE runs SET status=?, note=COALESCE(?, note) WHERE run_id=?", (status, note, run_id))


def finish_run(run_id: str, status: str, note: str | None = None) -> None:
    with tx() as c:
        c.execute(
            "UPDATE runs SET status=?, finished_at=?, note=COALESCE(?, note) WHERE run_id=?",
            (status, now(), note, run_id),
        )


def _run_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["request"] = json.loads(d.pop("request_json") or "{}")
    return d


def get_run(run_id: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return _run_row(r) if r else None


def list_runs(limit: int = 20) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [_run_row(r) for r in rows]


def last_unfinished_run() -> str | None:
    with conn() as c:
        r = c.execute(
            "SELECT run_id FROM runs WHERE status='running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return r["run_id"] if r else None


# ---------------------------------------------------------------- counterparties
def upsert_counterparty(cp: Counterparty) -> bool:
    with tx() as c:
        r = c.execute("SELECT record_hash FROM counterparties WHERE cp_id=?", (cp.cp_id,)).fetchone()
        if r and r["record_hash"] == cp.record_hash:
            return False
        c.execute(
            "INSERT OR REPLACE INTO counterparties(cp_id, json, record_hash, updated_at) VALUES(?,?,?,?)",
            (cp.cp_id, cp.model_dump_json(), cp.record_hash, now()),
        )
        return True


def get_counterparty(cp_id: str) -> Counterparty | None:
    with conn() as c:
        r = c.execute("SELECT json FROM counterparties WHERE cp_id=?", (cp_id,)).fetchone()
    return Counterparty.model_validate_json(r["json"]) if r else None


def list_counterparties() -> list[Counterparty]:
    with conn() as c:
        rows = c.execute("SELECT json FROM counterparties ORDER BY cp_id").fetchall()
    return [Counterparty.model_validate_json(r["json"]) for r in rows]


# ---------------------------------------------------------------- steps (checkpoints)
def step_status(run_id: str, cp_id: str, step: str) -> str | None:
    with conn() as c:
        r = c.execute(
            "SELECT status FROM steps WHERE run_id=? AND cp_id=? AND step=?", (run_id, cp_id, step)
        ).fetchone()
    return r["status"] if r else None


def mark_step(run_id: str, cp_id: str, step: str, status: str, output: dict | None = None) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO steps(run_id, cp_id, step, status, output_json, updated_at) VALUES(?,?,?,?,?,?)",
            (run_id, cp_id, step, status, json.dumps(output, default=str) if output is not None else None, now()),
        )


def get_step_output(run_id: str, cp_id: str, step: str) -> dict | None:
    with conn() as c:
        r = c.execute(
            "SELECT output_json FROM steps WHERE run_id=? AND cp_id=? AND step=?", (run_id, cp_id, step)
        ).fetchone()
    return json.loads(r["output_json"]) if r and r["output_json"] else None


def list_steps(run_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT cp_id, step, status, updated_at FROM steps WHERE run_id=? ORDER BY updated_at", (run_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- evidence
def save_evidence(ev: list[Evidence]) -> None:
    if not ev:
        return
    with tx() as c:
        c.executemany(
            "INSERT OR REPLACE INTO evidence(evidence_id, run_id, cp_id, signal, json) VALUES(?,?,?,?,?)",
            [(e.evidence_id, e.run_id, e.cp_id, e.signal, e.model_dump_json()) for e in ev],
        )


def get_evidence(run_id: str, cp_id: str) -> list[Evidence]:
    with conn() as c:
        rows = c.execute(
            "SELECT json FROM evidence WHERE run_id=? AND cp_id=? ORDER BY rowid", (run_id, cp_id)
        ).fetchall()
    return [Evidence.model_validate_json(r["json"]) for r in rows]


def get_evidence_by_ids(ids: list[str]) -> list[Evidence]:
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    with conn() as c:
        rows = c.execute(f"SELECT json FROM evidence WHERE evidence_id IN ({q})", ids).fetchall()
    return [Evidence.model_validate_json(r["json"]) for r in rows]


# ---------------------------------------------------------------- assessments
def save_assessment(a: Assessment) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO assessments(run_id, cp_id, rating, score, json, created_at) VALUES(?,?,?,?,?,?)",
            (a.run_id, a.cp_ids[0], a.rating.value, a.score, a.model_dump_json(), now()),
        )


def get_assessment(run_id: str, cp_id: str) -> Assessment | None:
    with conn() as c:
        r = c.execute("SELECT json FROM assessments WHERE run_id=? AND cp_id=?", (run_id, cp_id)).fetchone()
        if not r:  # cp_id may be a merged secondary id
            for row in c.execute("SELECT json FROM assessments WHERE run_id=?", (run_id,)).fetchall():
                a = Assessment.model_validate_json(row["json"])
                if cp_id in a.cp_ids:
                    return a
            return None
    return Assessment.model_validate_json(r["json"])


def get_assessments(run_id: str) -> list[Assessment]:
    with conn() as c:
        rows = c.execute("SELECT json FROM assessments WHERE run_id=?", (run_id,)).fetchall()
    out = [Assessment.model_validate_json(r["json"]) for r in rows]
    return sorted(out, key=lambda a: -a.inr_at_risk)


def previous_rating(cp_id: str, before_run_id: str) -> str | None:
    with conn() as c:
        r = c.execute(
            """SELECT a.rating FROM assessments a JOIN runs r ON a.run_id=r.run_id
               WHERE a.cp_id=? AND a.run_id != ? ORDER BY a.created_at DESC LIMIT 1""",
            (cp_id, before_run_id),
        ).fetchone()
    return r["rating"] if r else None


# ---------------------------------------------------------------- actions
def insert_action(a: Action) -> bool:
    with tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO actions(action_id, idempotency_key, run_id, cp_id, type, status, json, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (a.action_id, a.idempotency_key, a.run_id, a.cp_id, a.type.value, a.status.value,
             a.model_dump_json(), now()),
        )
        return cur.rowcount == 1


def get_action(action_id: str) -> Action | None:
    with conn() as c:
        r = c.execute("SELECT json FROM actions WHERE action_id=?", (action_id,)).fetchone()
    return Action.model_validate_json(r["json"]) if r else None


def get_action_by_key(idempotency_key: str) -> Action | None:
    with conn() as c:
        r = c.execute("SELECT json FROM actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
    return Action.model_validate_json(r["json"]) if r else None


def get_actions(status: str | None = None, run_id: str | None = None) -> list[Action]:
    q, p = "SELECT json FROM actions WHERE 1=1", []
    if status:
        q += " AND status=?"
        p.append(status)
    if run_id:
        q += " AND run_id=?"
        p.append(run_id)
    q += " ORDER BY updated_at"
    with conn() as c:
        rows = c.execute(q, p).fetchall()
    return [Action.model_validate_json(r["json"]) for r in rows]


def transition_action(action_id: str, from_status: str, to_status: str, **fields: Any) -> bool:
    """Compare-and-set. Returns False if the action is not currently in from_status."""
    from_status = getattr(from_status, "value", from_status)
    to_status = getattr(to_status, "value", to_status)
    with tx() as c:
        r = c.execute("SELECT json, status FROM actions WHERE action_id=?", (action_id,)).fetchone()
        if not r or r["status"] != from_status:
            return False
        data = json.loads(r["json"])
        for k, v in fields.items():
            data[k] = v.isoformat() if isinstance(v, datetime) else getattr(v, "value", v)
        data["status"] = to_status
        a = Action.model_validate(data)
        cur = c.execute(
            "UPDATE actions SET status=?, json=?, updated_at=? WHERE action_id=? AND status=?",
            (to_status, a.model_dump_json(), now(), action_id, from_status),
        )
        return cur.rowcount == 1


# ---------------------------------------------------------------- ledger
def insert_ledger(e: LedgerEntry) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO ledger(call_id, run_id, provider, endpoint, request_hash, credits, inr,"
            " tokens_in, tokens_out, latency_ms, status, note, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.call_id, e.run_id, e.provider, e.endpoint, e.request_hash, e.credits, e.inr, e.tokens_in,
             e.tokens_out, e.latency_ms, e.status, e.note, e.ts.isoformat()),
        )


def get_ledger(run_id: str | None = None, limit: int = 500) -> list[LedgerEntry]:
    q, p = "SELECT * FROM ledger", []
    if run_id:
        q += " WHERE run_id=?"
        p.append(run_id)
    q += " ORDER BY ts DESC LIMIT ?"
    p.append(limit)
    with conn() as c:
        rows = c.execute(q, p).fetchall()
    return [LedgerEntry.model_validate(dict(r)) for r in rows]


def run_costs(run_id: str) -> dict:
    with conn() as c:
        tot = c.execute(
            """SELECT COALESCE(SUM(credits),0) credits, COALESCE(SUM(inr),0) inr, COUNT(*) calls,
                      SUM(CASE WHEN status='cached' THEN 1 ELSE 0 END) cached,
                      SUM(CASE WHEN status IN ('error','timeout') THEN 1 ELSE 0 END) errors,
                      COALESCE(SUM(COALESCE(tokens_in,0) + COALESCE(tokens_out,0)),0) tokens
               FROM ledger WHERE run_id=?""",
            (run_id,),
        ).fetchone()
        by = c.execute(
            "SELECT endpoint, COUNT(*) calls, COALESCE(SUM(credits),0) credits, COALESCE(SUM(inr),0) inr"
            " FROM ledger WHERE run_id=? GROUP BY endpoint",
            (run_id,),
        ).fetchall()
    run = get_run(run_id)
    minutes = 0.0
    if run and run.get("started_at"):
        start = datetime.fromisoformat(run["started_at"])
        end = datetime.fromisoformat(run["finished_at"]) if run.get("finished_at") else datetime.now(timezone.utc)
        minutes = round(max((end - start).total_seconds(), 0) / 60, 1)
    calls, cached = tot["calls"], tot["cached"] or 0
    return {
        "credits": round(tot["credits"], 3),
        "inr": round(tot["inr"], 2),
        "calls": calls,
        "cached": cached,
        "cached_pct": round(100 * cached / calls, 1) if calls else 0.0,
        "errors": tot["errors"] or 0,
        "tokens": tot["tokens"],
        "minutes": minutes,
        "by_endpoint": {r["endpoint"]: {"calls": r["calls"], "credits": r["credits"], "inr": r["inr"]} for r in by},
    }


# ---------------------------------------------------------------- raw cache
def cache_get(request_hash: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT response_json FROM raw_cache WHERE request_hash=?", (request_hash,)).fetchone()
    return json.loads(r["response_json"]) if r else None


def cache_put(request_hash: str, endpoint: str, response: Any) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO raw_cache(request_hash, endpoint, response_json, ts) VALUES(?,?,?,?)",
            (request_hash, endpoint, json.dumps(response, default=str), now()),
        )


# ---------------------------------------------------------------- outbox / mock ERP / watch events
def outbox_insert(idempotency_key: str, channel: str, to_addr: str | None, subject: str | None, body: str) -> str | None:
    oid = _id("out")
    with tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO outbox(outbox_id, idempotency_key, channel, to_addr, subject, body, sent_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (oid, idempotency_key, channel, to_addr, subject, body, now()),
        )
        return oid if cur.rowcount == 1 else None


def outbox_get(idempotency_key: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM outbox WHERE idempotency_key=?", (idempotency_key,)).fetchone()
    return dict(r) if r else None


def list_outbox() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM outbox ORDER BY sent_at DESC").fetchall()]


def erp_hold_insert(idempotency_key: str, cp_id: str, reason: str) -> str | None:
    hid = _id("hold")
    with tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO erp_holds(hold_id, idempotency_key, cp_id, reason, created_at) VALUES(?,?,?,?,?)",
            (hid, idempotency_key, cp_id, reason, now()),
        )
        return hid if cur.rowcount == 1 else None


def erp_hold_get(idempotency_key: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM erp_holds WHERE idempotency_key=?", (idempotency_key,)).fetchone()
    return dict(r) if r else None


def list_erp_holds() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM erp_holds ORDER BY created_at DESC").fetchall()]


def save_watch_event(event_id: str, watch_id: str, payload: dict) -> bool:
    with tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO watch_events(event_id, watch_id, payload_json, received_at) VALUES(?,?,?,?)",
            (event_id, watch_id, json.dumps(payload, default=str), now()),
        )
        return cur.rowcount == 1
