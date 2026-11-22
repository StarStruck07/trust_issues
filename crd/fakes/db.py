"""Stand-in for crd/db.py (Arth): same function names, real SQLite underneath.

Real SQLite (not dicts) so the kill-mid-execute demo and the exactly-once tests behave
the same as they will against Arth's db: compare-and-set transitions, unique
idempotency keys on actions / outbox / ERP holds, survives a process kill.

Path: ``CRD_DB`` env var (read on every call, so tests can point it at tmp), default
``var/crd.db``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from crd.fakes.models import Action, Assessment, Evidence, LedgerEntry

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, request TEXT, as_of TEXT, status TEXT, started_at TEXT,
  finished_at TEXT, n INTEGER DEFAULT 0, doc TEXT);
CREATE TABLE IF NOT EXISTS assessments (
  run_id TEXT, cp_id TEXT, doc TEXT, PRIMARY KEY (run_id, cp_id));
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY, run_id TEXT, cp_id TEXT, doc TEXT);
CREATE TABLE IF NOT EXISTS actions (
  action_id TEXT PRIMARY KEY, run_id TEXT, cp_id TEXT, type TEXT,
  idempotency_key TEXT UNIQUE, reversible INTEGER, status TEXT, "to" TEXT, subject TEXT,
  draft TEXT, draft_hash TEXT, reason_evidence TEXT, approved_by TEXT, approved_at TEXT,
  executed_at TEXT, external_ref TEXT, error TEXT, note TEXT, created_at TEXT, updated_at TEXT,
  priority TEXT, why TEXT, requires_senior INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ledger (
  call_id TEXT PRIMARY KEY, run_id TEXT, provider TEXT, endpoint TEXT, request_hash TEXT,
  credits REAL, inr REAL, tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
  status TEXT, note TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  outbox_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, channel TEXT, "to" TEXT,
  subject TEXT, body TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS erp_holds (
  hold_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, cp_id TEXT, reason TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS watch_events (
  event_id TEXT PRIMARY KEY, payload TEXT, received_at TEXT);
"""

_ACTION_FIELDS = {
    "draft", "draft_hash", "approved_by", "approved_at", "executed_at", "external_ref",
    "error", "note", "to", "subject",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> str:
    return os.getenv("CRD_DB", os.path.join("var", "crd.db"))


@contextmanager
def _conn():
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript(SCHEMA)
    if os.getenv("CRD_FAKE_SEED", "1") == "1" and not list_runs():
        from crd.fakes import seed

        seed.seed_demo()


# ---------------------------------------------------------------- runs / results
def create_run(run_id: str, request: str, as_of: str, status: str = "running", n: int = 0) -> None:
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO runs (run_id, request, as_of, status, started_at, n, doc) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, request, as_of, status, now(), n, "{}"),
        )


def update_run(run_id: str, **fields) -> None:
    allowed = {"status", "finished_at", "n", "doc"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with _lock, _conn() as c:
        c.execute(
            f"UPDATE runs SET {', '.join(f'{k}=?' for k in sets)} WHERE run_id=?",
            (*sets.values(), run_id),
        )


def _run_row(r) -> dict:
    d = dict(r)
    d["doc"] = json.loads(d.get("doc") or "{}")
    return d


def list_runs(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [_run_row(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return _run_row(r) if r else None


def save_assessment(a: Assessment) -> None:
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO assessments (run_id, cp_id, doc) VALUES (?,?,?)",
            (a.run_id, a.cp_ids[0], a.model_dump_json()),
        )


def get_assessments(run_id: str) -> list[Assessment]:
    with _conn() as c:
        rows = c.execute("SELECT doc FROM assessments WHERE run_id=?", (run_id,)).fetchall()
    return [Assessment.model_validate_json(r["doc"]) for r in rows]


def get_assessment(run_id: str, cp_id: str) -> Assessment | None:
    for a in get_assessments(run_id):
        if cp_id in a.cp_ids:
            return a
    return None


def save_evidence(e: Evidence) -> None:
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO evidence (evidence_id, run_id, cp_id, doc) VALUES (?,?,?,?)",
            (e.evidence_id, e.run_id, e.cp_id, e.model_dump_json()),
        )


def get_evidence(run_id: str, cp_id: str | None = None) -> list[Evidence]:
    q, args = "SELECT doc FROM evidence WHERE run_id=?", [run_id]
    if cp_id:
        q += " AND cp_id=?"
        args.append(cp_id)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [Evidence.model_validate_json(r["doc"]) for r in rows]


def get_evidence_by_ids(ids: list[str]) -> list[Evidence]:
    if not ids:
        return []
    with _conn() as c:
        rows = c.execute(
            f"SELECT doc FROM evidence WHERE evidence_id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
    by_id = {e.evidence_id: e for e in (Evidence.model_validate_json(r["doc"]) for r in rows)}
    return [by_id[i] for i in ids if i in by_id]


# ---------------------------------------------------------------- actions
def _action(r) -> Action:
    d = dict(r)
    d["reversible"] = bool(d["reversible"])
    d["requires_senior"] = bool(d.get("requires_senior") or 0)
    d["reason_evidence"] = json.loads(d.get("reason_evidence") or "[]")
    for k in ("note", "created_at", "updated_at"):
        d.pop(k, None)
    return Action(**d)


def insert_action(a: Action) -> bool:
    """False if the idempotency key already exists."""
    with _lock, _conn() as c:
        try:
            c.execute(
                'INSERT INTO actions (action_id, run_id, cp_id, type, idempotency_key, reversible, '
                'status, "to", subject, draft, draft_hash, reason_evidence, approved_by, '
                "approved_at, executed_at, external_ref, error, created_at, updated_at, priority, why, "
                "requires_senior) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    a.action_id, a.run_id, a.cp_id, a.type.value, a.idempotency_key,
                    int(a.reversible), a.status.value, a.to, a.subject, a.draft, a.draft_hash,
                    json.dumps(a.reason_evidence), a.approved_by, a.approved_at, a.executed_at,
                    a.external_ref, a.error, now(), now(), getattr(a, "priority", None),
                    getattr(a, "why", None), int(bool(getattr(a, "requires_senior", False))),
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_action(action_id: str) -> Action | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
    return _action(r) if r else None


def get_action_by_key(idempotency_key: str) -> Action | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
    return _action(r) if r else None


def get_actions(status: str | list[str] | None = None, run_id: str | None = None) -> list[Action]:
    q, args = "SELECT * FROM actions WHERE 1=1", []
    if status:
        sts = [status] if isinstance(status, str) else list(status)
        q += f" AND status IN ({','.join('?' * len(sts))})"
        args += [getattr(s, "value", s) for s in sts]
    if run_id:
        q += " AND run_id=?"
        args.append(run_id)
    q += " ORDER BY updated_at DESC, created_at DESC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_action(r) for r in rows]


def transition_action(action_id: str, from_status: str, to_status: str, **fields) -> bool:
    """Compare-and-set: only moves if the current status is ``from_status``."""
    sets = {k: v for k, v in fields.items() if k in _ACTION_FIELDS}
    sets["status"] = getattr(to_status, "value", to_status)
    sets["updated_at"] = now()
    cols = ", ".join(f'"{k}"=?' for k in sets)
    with _lock, _conn() as c:
        cur = c.execute(
            f"UPDATE actions SET {cols} WHERE action_id=? AND status=?",
            (*sets.values(), action_id, getattr(from_status, "value", from_status)),
        )
        return cur.rowcount == 1


# ---------------------------------------------------------------- ledger
def add_ledger(e: LedgerEntry) -> None:
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.call_id, e.run_id, e.provider, e.endpoint, e.request_hash, e.credits, e.inr,
             e.tokens_in, e.tokens_out, e.latency_ms, e.status, e.note, e.ts or now()),
        )


def get_ledger(run_id: str | None = None) -> list[LedgerEntry]:
    q, args = "SELECT * FROM ledger", []
    if run_id:
        q += " WHERE run_id=?"
        args.append(run_id)
    q += " ORDER BY ts DESC, call_id DESC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [LedgerEntry(**dict(r)) for r in rows]


def run_costs(run_id: str | None = None) -> dict:
    rows = get_ledger(run_id)
    calls = len(rows)
    run = get_run(run_id) if run_id else None
    minutes = 0.0
    if run and run.get("started_at") and run.get("finished_at"):
        a = datetime.fromisoformat(run["started_at"])
        b = datetime.fromisoformat(run["finished_at"])
        minutes = round((b - a).total_seconds() / 60, 1)
    return {
        "credits": round(sum(r.credits for r in rows), 2),
        "inr": round(sum(r.inr for r in rows), 2),
        "calls": calls,
        "cached": sum(1 for r in rows if r.status == "cached"),
        "cached_pct": round(100 * sum(1 for r in rows if r.status == "cached") / calls, 1) if calls else 0.0,
        "errors": sum(1 for r in rows if r.status == "error"),
        "tokens": sum(r.tokens_in + r.tokens_out for r in rows),
        "minutes": minutes,
    }


# ---------------------------------------------------------------- channels
def outbox_insert(key: str, channel: str, to: str | None, subject: str | None, body: str) -> str | None:
    """Returns the new outbox_id, or None if this idempotency key was already written."""
    oid = "ob_" + uuid.uuid4().hex[:8]
    with _lock, _conn() as c:
        try:
            c.execute(
                'INSERT INTO outbox (outbox_id, idempotency_key, channel, "to", subject, body, created_at) '
                "VALUES (?,?,?,?,?,?,?)",
                (oid, key, channel, to, subject, body, now()),
            )
            return oid
        except sqlite3.IntegrityError:
            return None


def outbox_get(key: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM outbox WHERE idempotency_key=?", (key,)).fetchone()
    return dict(r) if r else None


def list_outbox(limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM outbox ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def erp_hold_insert(key: str, cp_id: str, reason: str) -> str | None:
    hid = "hold_" + uuid.uuid4().hex[:8]
    with _lock, _conn() as c:
        try:
            c.execute(
                "INSERT INTO erp_holds (hold_id, idempotency_key, cp_id, reason, created_at) VALUES (?,?,?,?,?)",
                (hid, key, cp_id, reason, now()),
            )
            return hid
        except sqlite3.IntegrityError:
            return None


def erp_hold_get(key: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM erp_holds WHERE idempotency_key=?", (key,)).fetchone()
    return dict(r) if r else None


def list_erp_holds(limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM erp_holds ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_watch_event(event_id: str, payload: str) -> bool:
    """False if this event id was already received (duplicate webhook delivery)."""
    with _lock, _conn() as c:
        try:
            c.execute(
                "INSERT INTO watch_events (event_id, payload, received_at) VALUES (?,?,?)",
                (event_id, payload, now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False
