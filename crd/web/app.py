"""Counterparty Risk Desk web UI (PRACHI_product.md section 5).

Run: uvicorn crd.web.app:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from crd.actions import executor
from crd.actions.policy import HUMAN_LABEL, IRREVERSIBLE, sha256
from crd.deps import USING_FAKE_DB, config, db, explain, orchestrator, watcher
from crd.report import render as report

log = logging.getLogger("crd.web")
HERE = Path(__file__).parent
VAR = Path(os.getenv("CRD_VAR", "var"))
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
STEPS = ["resolve", "gather", "score", "propose"]
DEFAULT_REQUEST = "Run this week's risk review on all our carriers and customers"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    executor.reconcile()  # settle anything a kill left in 'executing'
    _bg(orchestrator.resume)
    _bg(executor.execute_approved)  # actions reconcile moved back to 'approved'
    yield


app = FastAPI(title="Counterparty Risk Desk", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))


# ---------------------------------------------------------------- template helpers
def _val(v):
    return getattr(v, "value", v)


def src_link(e) -> Markup:
    """`source ↗ · record_date` for one Evidence (url, else raw JSON, else plain text)."""
    href = report.source_href(e)
    date = escape(e.record_date or "no date")
    if href:
        return Markup(f'<a class="src" href="{escape(href)}" target="_blank">source ↗</a> · {date}')
    return Markup(f'<span class="muted">{escape(e.source or "no link")}</span> · {date}')


def evlinks(text: str) -> Markup:
    """Turn [ev_xxx] markers into superscript links to the evidence row."""
    seen: dict[str, int] = {}

    def cite(m):
        eid = m.group(1)
        n = seen.setdefault(eid, len(seen) + 1)
        return f'<a class="cite" href="#{eid}" title="{eid}">{n}</a>'

    return Markup(re.sub(r"\s*\[(ev_[A-Za-z0-9]+)\]", cite, str(escape(text or ""))))


STATUS_LABEL = {
    "proposed": "Draft only", "pending_approval": "Awaiting approval", "approved": "Approved · queued",
    "executing": "Sending…", "done": "Done", "rejected": "Rejected", "held": "Held", "failed": "Failed",
    "running": "Running", "partial": "Partial", "ok": "OK", "cached": "Cached", "error": "Error",
    "unknown": "Unknown",
}
HUMAN = {
    "headcount_qoq": "Headcount · quarter", "headcount_6m": "Headcount · 6 months", "hiring": "Hiring",
    "news": "News", "leadership": "Leadership", "insolvency": "Insolvency", "mca_status": "MCA status",
    "identity": "Identity", "warehouse_lessor": "warehouse lessor", "layoffs_or_shutdown": "Layoffs or shutdown reported",
    "cirp_admitted": "insolvency (CIRP) admitted", "sole_source": "sole source",
}


def status_label(s) -> str:
    s = _val(s)
    return STATUS_LABEL.get(s, str(s).replace("_", " ").capitalize())


def human(s) -> str:
    s = str(_val(s) or "")
    return HUMAN.get(s, s.replace("_", " "))


def reason(text) -> str:
    """Make a rule note readable: 'qoq -22.1% <= -20% -> -20' -> 'qoq -22.1% ≤ -20%'."""
    t = str(text or "")
    if t.startswith("hard stop: "):
        return "Hard stop: " + human(t[len("hard stop: "):])
    if t in HUMAN:
        return HUMAN[t]
    t = re.sub(r"\s*->\s*[+-]?\d+\s*$", "", t).replace("<=", "≤").replace(">=", "≥").replace("->", "→")
    return t[:1].upper() + t[1:]


def move(text) -> Markup:
    """'AMBER → RED' as two small rating pills with an arrow."""
    parts = re.split(r"\s*(?:→|->)\s*", str(text or ""))
    if len(parts) != 2:
        return Markup(escape(text or ""))
    pill = lambda r: (f'<span class="pill sm r-{escape(r.strip().replace(" ", "_"))}">'
                      f'{escape(report.rating_label(r.strip()))}</span>')
    return Markup(f'<span class="move">{pill(parts[0])}<span class="arrow">→</span>{pill(parts[1])}</span>')


def action_label(x) -> str:
    if (getattr(x, "idempotency_key", "") or "").endswith(":account_call"):
        return "Account owner call (instead of a terms change)"
    t = _val(x.type)
    return HUMAN_LABEL.get(t, t)


env = templates.env
env.filters["action_label"] = action_label
env.filters["move"] = move
env.filters["inr"] = report.inr
env.filters["status_label"] = status_label
env.filters["human"] = human
env.filters["reason"] = reason
env.filters["href"] = report.source_href
env.globals["css_v"] = lambda: int((HERE / "static" / "style.css").stat().st_mtime)
env.globals["MOCK"] = USING_FAKE_DB or os.getenv("CRD_MOCK", "1") == "1"
env.filters["rating_label"] = report.rating_label
env.filters["val"] = _val
env.filters["src"] = src_link
env.filters["evlinks"] = evlinks
env.globals["HUMAN_LABEL"] = HUMAN_LABEL
env.globals["STEPS"] = STEPS
env.globals["USING_FAKE_DB"] = USING_FAKE_DB


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    try:
        pending = len(db.get_actions("pending_approval"))
    except Exception:
        pending = 0
    return templates.TemplateResponse(request, name, {"pending_count": pending, **ctx})


def _latest_run():
    runs = db.list_runs()
    return runs[0] if runs else None


def _counts(assessments) -> dict:
    c = {"RED": 0, "AMBER": 0, "CV": 0, "GREEN": 0}
    for a in assessments:
        r = _val(a.rating)
        k = "CV" if r in ("CANNOT_VERIFY", "HELD") else r
        c[k] = c.get(k, 0) + 1
    return c


# ---------------------------------------------------------------- dashboard / runs
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    run = _latest_run()
    data = report.build(run["run_id"]) if run else None
    return page(request, "dashboard.html", run=run, data=data, default_request=DEFAULT_REQUEST,
                counts=_counts(data["assessments"]) if data else None)


@app.post("/runs")
async def start_run(request_text: str = Form(DEFAULT_REQUEST), csv_file: UploadFile | None = File(None)):
    path = None
    if csv_file is not None and csv_file.filename:
        up = VAR / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", csv_file.filename)[:80]
        dest = up / f"{uuid.uuid4().hex[:8]}_{safe}"
        dest.write_bytes(await csv_file.read())
        path = str(dest)
    run_id = orchestrator.run_review_async(request_text.strip() or DEFAULT_REQUEST, path)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    rows = [{**r, "costs": db.run_costs(r["run_id"])} for r in db.list_runs()]
    return page(request, "runs.html", runs=rows)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_live(request: Request, run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return page(request, "run_live.html", run=run, prog=orchestrator.run_progress(run_id))


@app.get("/runs/{run_id}/progress", response_class=HTMLResponse)
def run_progress(request: Request, run_id: str):
    run = db.get_run(run_id) or {"run_id": run_id, "status": "unknown"}
    return templates.TemplateResponse(request, "_progress.html",
                                      {"run": run, "prog": orchestrator.run_progress(run_id)})


@app.get("/runs/{run_id}/report", response_class=HTMLResponse)
def run_report(request: Request, run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "run not found")
    report.render(run_id)  # always fresh: approvals change action_status
    return page(request, "report.html", run_id=run_id)


@app.get("/runs/{run_id}/files/{name}")
def run_file(run_id: str, name: str):
    if name not in ("review.html", "review.json", "review.md", "ledger.csv") or not SAFE_NAME.match(run_id):
        raise HTTPException(404)
    p = report.run_dir(run_id) / name
    if not p.exists():
        report.render(run_id)
    media = {"html": "text/html", "json": "application/json", "md": "text/markdown",
             "csv": "text/csv"}[name.rsplit(".", 1)[1]]
    headers = {} if name == "review.html" else {"Content-Disposition": f'attachment; filename="{run_id}_{name}"'}
    return FileResponse(p, media_type=media, headers=headers)


# ---------------------------------------------------------------- counterparty detail
@app.get("/cp/{run_id}/{cp_id}", response_class=HTMLResponse)
def cp_detail(request: Request, run_id: str, cp_id: str):
    a = db.get_assessment(run_id, cp_id)
    if not a:
        raise HTTPException(404, "assessment not found")
    evidence = [e for cp in a.cp_ids for e in db.get_evidence(run_id, cp)]
    actions = [x for x in db.get_actions(None, run_id) if x.cp_id in a.cp_ids]
    from crd.actions import priority

    return page(request, "cp_detail.html", a=a, why=explain.why(run_id, cp_id) or {},
                prio=priority.for_assessment(a, cfg=config.get()),
                evidence=evidence, actions=actions, run_id=run_id)


# ---------------------------------------------------------------- approvals
def _reasons(act) -> list:
    return db.get_evidence_by_ids(list(act.reason_evidence or []))


def _assessment_for(act):
    try:
        return db.get_assessment(act.run_id, act.cp_id)
    except Exception:
        return None


@app.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, done: str | None = None, err: str | None = None,
              decision: str | None = None):
    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    pending = [{"act": x, "a": _assessment_for(x), "reasons": _reasons(x)}
               for x in db.get_actions("pending_approval")]
    pending.sort(key=lambda p: order.get(getattr(p["act"], "priority", None) or "P4", 3))
    decided = [x for x in db.get_actions(["approved", "executing", "done", "rejected", "failed"])
               if not x.reversible][:10]
    return page(request, "approvals.html", pending=pending, decided=decided, done=done, err=err,
                decision=decision)


@app.post("/approvals/{action_id}")
def decide(action_id: str, decision: str = Form(...), approver: str = Form(""), draft: str = Form(""),
           senior: str = Form("")):
    approver = approver.strip()
    if not approver:
        return RedirectResponse(f"/approvals?err=approver#{action_id}", status_code=303)
    act = db.get_action(action_id)
    if not act:
        raise HTTPException(404, "action not found")
    if decision == "approve":
        if getattr(act, "requires_senior", False):
            if senior != "yes":
                return RedirectResponse(f"/approvals?err=senior#{action_id}", status_code=303)
            approver = f"{approver} (senior)"
        draft = draft.replace("\r\n", "\n")
        ok = db.transition_action(action_id, "pending_approval", "approved", draft=draft,
                                  draft_hash=sha256(draft), approved_by=approver, approved_at=_now(),
                                  error=None)
        if not ok:
            return RedirectResponse(f"/approvals?err=changed#{action_id}", status_code=303)
        _bg(executor.execute_approved)
    elif decision == "reject":
        ok = db.transition_action(action_id, "pending_approval", "rejected", approved_by=approver,
                                  approved_at=_now())
        if not ok:
            return RedirectResponse(f"/approvals?err=changed#{action_id}", status_code=303)
    else:
        raise HTTPException(400, "decision must be approve or reject")
    return RedirectResponse(f"/approvals?done={action_id}&decision={decision}", status_code=303)


@app.post("/actions/{action_id}/retry")
def retry(action_id: str):
    executor.retry(action_id)
    return RedirectResponse("/approvals", status_code=303)


# ---------------------------------------------------------------- outbox / ledger
@app.get("/outbox", response_class=HTMLResponse)
def outbox(request: Request):
    msgs, holds = db.list_outbox(), db.list_erp_holds()
    keys = [m["idempotency_key"] for m in msgs] + [h["idempotency_key"] for h in holds]
    executed = len(db.get_actions("done"))
    return page(request, "outbox.html", msgs=msgs, holds=holds, executed=executed,
                unique=len(keys) == len(set(keys)))


@app.get("/ledger", response_class=HTMLResponse)
def ledger(request: Request, run: str | None = None):
    run = run or None
    return page(request, "ledger.html", rows=db.get_ledger(run), costs=db.run_costs(run),
                runs=db.list_runs(), run=run)


@app.get("/ledger.csv")
def ledger_csv(run: str | None = None):
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["ts", "run_id", "provider", "endpoint", "credits", "inr", "tokens_in", "tokens_out",
            "latency_ms", "status", "note"]
    w.writerow(cols)
    for le in db.get_ledger(run or None):
        w.writerow([getattr(le, k, "") for k in cols])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ledger_{run or "all"}.csv"'})


# ---------------------------------------------------------------- raw / webhook / health
@app.get("/raw/{run_id}/{file}")
def raw(run_id: str, file: str):
    for part in (run_id, file):
        if not SAFE_NAME.match(part) or ".." in part:
            raise HTTPException(400, "bad path")
    p = VAR / "runs" / run_id / "raw" / file
    if not p.is_file():
        if USING_FAKE_DB:
            return JSONResponse({"mock": True, "run_id": run_id, "file": file,
                                 "note": "stand-in: raw responses are stored by the real gatherer"})
        raise HTTPException(404, "raw response not stored")
    return FileResponse(p, media_type="application/json")


@app.post("/webhooks/crustdata")
async def crustdata_webhook(request: Request):
    raw_body = await request.body()
    if not watcher.verify_signature(request.headers, raw_body):
        return JSONResponse({"ok": False}, status_code=401)
    try:
        body = json.loads(raw_body or b"{}")
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
    event_id = (request.headers.get("x-crustdata-event-id") or body.get("event_id")
                or hashlib.sha256(raw_body).hexdigest()[:24])
    if not db.save_watch_event(event_id, raw_body.decode("utf-8", "replace")):
        return {"ok": True, "duplicate": True}
    domains = watcher.parse_payload(body)
    for d in domains:
        _bg(orchestrator.run_single, d, "watcher")
    return {"ok": True, "queued": domains}


@app.get("/health")
def health():
    return {"ok": True, "mock": os.getenv("CRD_MOCK", "1") == "1", "fake_db": USING_FAKE_DB}
