"""Run pipeline with step checkpoints. Owner: Arth (P1).

resolve -> gather -> score -> propose for each entity, then execute approved actions and render.
Each (run, cp, step) is checkpointed in SQLite. Kill at any point; resume() skips finished steps,
reconciles half-executed actions first, and never repeats an action (idempotency keys).
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from typing import Callable, Optional

from crd import config, db, explain, request_parser, scoring
from crd.data import people, signals
from crd.data.crustdata import CrustdataClient
from crd.data.errors import BudgetExceeded, CreditsExhausted
from crd.models import Action, Assessment, Counterparty, Evidence, ResolvedEntity, ReviewRequest, SUPPLIER_ROLES
from crd.resolve import resolve_all

# crd.actions.* and crd.report.* are imported inside the functions that use them:
# they import crd.deps, which imports this module, so a top-level import is circular.

STEPS = ["resolve", "gather", "score", "propose"]
_lock = threading.Lock()


def _client(run_id: str):
    """Factory hook so tests can inject a fake client."""
    return CrustdataClient(run_id)


def _select(cps: list[Counterparty], req: ReviewRequest) -> list[Counterparty]:
    if req.scope == "ids":
        return [c for c in cps if c.cp_id in set(req.cp_ids)]
    if req.scope == "suppliers":
        return [c for c in cps if c.role in SUPPLIER_ROLES]
    if req.scope == "customers":
        return [c for c in cps if c.role.value == "customer"]
    return cps


def _cached(run_id: str, cp_id: str, step: str, fn: Callable[[], dict]) -> dict:
    if db.step_status(run_id, cp_id, step) == "done":
        out = db.get_step_output(run_id, cp_id, step)
        if out is not None:
            return out
    db.mark_step(run_id, cp_id, step, "running")
    out = fn()
    db.mark_step(run_id, cp_id, step, "done", out)
    return out


def _resolve_step(run_id: str, cps: list[Counterparty], client, cfg) -> list[dict]:
    def fn() -> dict:
        groups = resolve_all(cps, client, run_id, cfg)
        allev = [e for _, _, evs in groups for e in evs]
        db.save_evidence(allev)
        return {"groups": [{"entity": ent.model_dump(mode="json"), "cp_ids": [c.cp_id for c in grp],
                            "evidence_ids": [e.evidence_id for e in evs]} for ent, grp, evs in groups]}
    return _cached(run_id, "__all__", "resolve", fn)["groups"]


def process_entity(run_id: str, entity: ResolvedEntity, group: list[Counterparty], id_ev_ids: list[str],
                   client, cfg, req: ReviewRequest) -> Assessment:
    cid = group[0].cp_id

    def gather() -> dict:
        ev = list(signals.gather(group[0], entity, client, cfg)) if entity.status == "resolved" else \
            _client_record_only(group[0], entity, run_id, cfg)
        for e in ev:
            e.cp_id = cid
        db.save_evidence(ev)
        return {"evidence_ids": id_ev_ids + [e.evidence_id for e in ev]}

    g = _cached(run_id, cid, "gather", gather)
    evidence = db.get_evidence_by_ids(g["evidence_ids"])

    def score() -> dict:
        a = scoring.score(group, entity, evidence, cfg, run_id, db.previous_rating(cid, run_id))
        a.explanation = explain.write(a, evidence, run_id)
        db.save_assessment(a)
        return {"assessment": a.model_dump(mode="json")}

    a = Assessment.model_validate(_cached(run_id, cid, "score", score)["assessment"])

    def propose() -> dict:
        from crd.actions import policy
        acts: list[Action] = policy.propose(a, group[0], cfg, config.review_id(req.as_of)) or []
        a.action_ids = [x.action_id for x in acts]
        db.save_assessment(a)
        return {"action_ids": a.action_ids}

    p = _cached(run_id, cid, "propose", propose)
    a.action_ids = p["action_ids"]
    return a


def _client_record_only(cp: Counterparty, entity: ResolvedEntity, run_id: str, cfg) -> list[Evidence]:
    """Unresolved identity: only the client's own receivables record is safe to use."""
    import uuid
    from datetime import datetime, timezone
    if cp.dso_days is None or cp.credit_terms_days is None:
        return []
    return [Evidence(
        evidence_id=f"ev_{uuid.uuid4().hex[:8]}", run_id=run_id, cp_id=cp.cp_id, signal="payment_behaviour",
        claim=f"DSO {cp.dso_days:.0f} days against {cp.credit_terms_days}-day terms",
        value={"dso": cp.dso_days, "terms": cp.credit_terms_days, "overdue_inr": cp.overdue_inr},
        source_type="client_record", source="client receivables record", field_path="dso_days",
        record_date=config.as_of(cfg), retrieved_at=datetime.now(timezone.utc))]


def run_review(raw_text: str, input_path: Optional[str] = None, run_id: Optional[str] = None,
               scope_override: Optional[dict] = None) -> str:
    db.init_db()
    cfg = config.get()
    row_errors: list[str] = []
    if run_id and db.get_run(run_id):
        req = ReviewRequest.model_validate(db.get_run(run_id)["request"])
        input_path = input_path or req.input_path
        if input_path and os.path.exists(input_path):
            from crd.inputs import load_counterparties
            _, row_errors = load_counterparties(input_path)
        cps = db.list_counterparties()
    else:
        if input_path:
            from crd.inputs import load_counterparties
            _, row_errors = load_counterparties(input_path)
        cps = db.list_counterparties()
        req = request_parser.parse(raw_text, cps, cfg)
        if scope_override:
            req = req.model_copy(update=scope_override)
        req.input_path = input_path
        run_id = db.new_run(req, run_id)
        if req.clarification:
            db.finish_run(run_id, "needs_clarification", note=req.clarification)
            return run_id
    if row_errors:
        db.set_run_status(run_id, "running", note="; ".join(row_errors)[:1000])

    client = _client(run_id)
    selected = _select(cps, req)
    deadline = time.time() + 60 * float(cfg.limits.max_minutes)
    status, note = "done", None
    try:
        groups = _resolve_step(run_id, selected, client, cfg)
        by_id = {c.cp_id: c for c in cps}
        for i, g in enumerate(groups):
            entity = ResolvedEntity.model_validate(g["entity"])
            group = [by_id[x] for x in g["cp_ids"] if x in by_id]
            if not group:
                continue
            if time.time() > deadline:
                status, note = "partial", f"time cap hit; {len(groups) - i} entities not reviewed"
                break
            try:
                process_entity(run_id, entity, group, g["evidence_ids"], client, cfg, req)
            except (BudgetExceeded, CreditsExhausted) as e:
                status = "partial"
                note = f"{type(e).__name__}: {e}; not reviewed: " + ", ".join(
                    x["cp_ids"][0] for x in groups[i:])
                break
        from crd.actions import executor
        from crd.report import render
        executor.execute_approved(run_id)
        render.render(run_id)
    except Exception as e:
        traceback.print_exc()
        status, note = "failed", f"{type(e).__name__}: {e}"
    finally:
        try:
            people.purge_people_raw(run_id)
        except Exception:
            pass
    db.finish_run(run_id, status, note)
    return run_id


def run_review_async(raw_text: str, input_path: Optional[str] = None) -> str:
    """Creates the run row first (so the UI can redirect), then runs in a background thread."""
    db.init_db()
    cfg = config.get()
    if input_path:
        from crd.inputs import load_counterparties
        load_counterparties(input_path)
    req = request_parser.parse(raw_text, db.list_counterparties(), cfg)
    req.input_path = input_path
    rid = db.new_run(req)
    if req.clarification:
        db.finish_run(rid, "needs_clarification", note=req.clarification)
        return rid
    threading.Thread(target=run_review, kwargs={"raw_text": raw_text, "run_id": rid}, daemon=True).start()
    return rid


def run_single(domain_or_cp_id: str, trigger: str = "watcher") -> str | None:
    key = domain_or_cp_id.lower().strip()
    cp = next((c for c in db.list_counterparties()
               if c.cp_id.lower() == key or (c.domain or "").lower() == key), None)
    if not cp:
        return None
    return run_review(f"Re-check {cp.name} ({trigger})", None,
                      scope_override={"scope": "ids", "cp_ids": [cp.cp_id], "trigger": trigger,
                                      "clarification": None})


def resume() -> str | None:
    from crd.actions import executor
    db.init_db()
    executor.reconcile()
    rid = db.last_unfinished_run()
    if not rid:
        executor.execute_approved()
        return None
    return run_review("", None, run_id=rid)


def run_progress(run_id: str) -> dict:
    run = db.get_run(run_id) or {}
    flat = db.list_steps(run_id)
    groups = db.get_step_output(run_id, "__all__", "resolve") or {"groups": []}
    # One row per entity (keyed by its first cp_id), one column per step, for the live page.
    # Resolve is batched under "__all__", so every row shares its status.
    resolve_all_status = next((s["status"] for s in flat if s["cp_id"] == "__all__" and s["step"] == "resolve"),
                              "running" if run.get("status") == "running" else None)
    names = {c.cp_id: c.name for c in db.list_counterparties()}
    rows: dict[str, dict] = {}
    for g in groups["groups"]:
        cid = g["cp_ids"][0]
        rows[cid] = {"cp_id": cid, "display_name": names.get(cid), "resolve": resolve_all_status}
    for s in flat:
        if s["cp_id"] == "__all__":
            continue
        rows.setdefault(s["cp_id"], {"cp_id": s["cp_id"], "display_name": names.get(s["cp_id"]),
                                     "resolve": resolve_all_status})[s["step"]] = s["status"]
    for r in rows.values():
        for st in STEPS:
            r.setdefault(st, None)
    done = sum(1 for r in rows.values() if r["propose"] == "done")
    ledger = [l.model_dump(mode="json") for l in db.get_ledger(run_id, limit=10)]
    return {
        "run_id": run_id, "status": run.get("status"), "note": run.get("note"),
        "request": run.get("request", {}).get("raw_text"),
        "total": len(rows), "done": done, "steps": list(rows.values()), "costs": db.run_costs(run_id),
        "ledger": ledger, "recent_calls": ledger,
    }
