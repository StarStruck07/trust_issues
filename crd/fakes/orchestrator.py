"""Stand-in for crd/orchestrator.py (Arth): replays the seed data step by step so the
live progress page has something to animate in mock mode."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

from crd.fakes import db, seed

log = logging.getLogger("crd.fakes.orchestrator")
STEPS = ["resolve", "gather", "score", "propose"]


def _step_delay() -> float:
    return float(os.getenv("CRD_FAKE_STEP_SEC", "0.6"))


def _progress(run_id: str) -> dict:
    run = db.get_run(run_id) or {}
    return run.get("doc", {}).get("progress", {})


def _save_progress(run_id: str, prog: dict) -> None:
    run = db.get_run(run_id)
    doc = run["doc"] if run else {}
    doc["progress"] = prog
    db.update_run(run_id, doc=json.dumps(doc))


def run_review_async(text: str, csv_path: str | None = None) -> str:
    run_id = "run_" + uuid.uuid4().hex[:8]
    db.create_run(run_id, text, "2026-11-22", status="running", n=len(seed.COUNTERPARTIES))
    A, _ = seed.build(run_id)
    prog = {a.cp_ids[0]: {"display_name": a.display_name, **{s: "pending" for s in STEPS}} for a in A}
    _save_progress(run_id, prog)
    threading.Thread(target=_work, args=(run_id,), daemon=True).start()
    return run_id


def _work(run_id: str) -> None:
    from crd.actions import executor, policy
    from crd.deps import config
    from crd.report import render

    A, E = seed.build(run_id)
    ev_by_cp: dict[str, list] = {}
    for e in E:
        ev_by_cp.setdefault(e.cp_id, []).append(e)
    rv = config.review_id("2026-11-22")
    prog = _progress(run_id)
    done_before = {a.cp_ids[0] for a in db.get_assessments(run_id)}
    try:
        for a in A:
            cid = a.cp_ids[0]
            if cid in done_before:
                continue
            for step in STEPS:
                prog[cid][step] = "running"
                _save_progress(run_id, prog)
                time.sleep(_step_delay())
                if step == "gather":
                    for cp in a.cp_ids:
                        for e in ev_by_cp.get(cp, []):
                            db.save_evidence(e)
                    for le in seed.ledger_rows(run_id, cid, cached=cid == "C-101"):
                        db.add_ledger(le)
                        time.sleep(_step_delay() / 6)
                if step == "propose":
                    acts = policy.propose(a, seed.cp_by_id(cid), config.get(), rv)
                    a.action_ids = [x.action_id for x in acts]
                    db.save_assessment(a)
                prog[cid][step] = "error" if (cid == "C-105" and step == "gather") else "done"
                _save_progress(run_id, prog)
        executor.execute_approved(run_id)
        db.update_run(run_id, status="done", finished_at=db.now())
        render.render(run_id)
    except Exception:
        log.exception("run %s failed", run_id)
        db.update_run(run_id, status="partial", finished_at=db.now())


def run_progress(run_id: str) -> dict:
    run = db.get_run(run_id) or {}
    prog = _progress(run_id)
    rows = [{"cp_id": k, **v} for k, v in prog.items()]
    done = sum(1 for r in rows if all(r[s] in ("done", "error") for s in STEPS))
    return {
        "status": run.get("status", "unknown"),
        "done": done,
        "total": len(rows) or run.get("n", 0),
        "steps": rows,
        "ledger": [le.model_dump() for le in db.get_ledger(run_id)[:10]],
        "costs": db.run_costs(run_id),
    }


def resume() -> None:
    for r in db.list_runs():
        if r["status"] == "running":
            log.info("resuming %s", r["run_id"])
            threading.Thread(target=_work, args=(r["run_id"],), daemon=True).start()


def run_single(domain: str, source: str = "watcher") -> str:
    return run_review_async(f"Re-check {domain} ({source} event)")
