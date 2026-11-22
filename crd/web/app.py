"""Trust Issues web UI (PRACHI_product.md section 5).

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


app = FastAPI(title="Trust Issues", lifespan=lifespan)
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
    "rating": "verdict", "score": "trust score", "action_status": "next step", "display_name": "company",
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


def ts(v, n: int = 19) -> str:
    """'2026-11-22T09:13:55.12+00:00' or a datetime -> '2026-11-22 09:13:55' (n chars)."""
    if v is None or v == "":
        return "—"
    s = v.isoformat() if hasattr(v, "isoformat") else str(v)
    return s.replace("T", " ")[:n]


def clock(v) -> str:
    s = ts(v)
    return s[11:19] if len(s) >= 19 else s


env = templates.env
env.filters["ts"] = ts
env.filters["clock"] = clock
env.filters["action_label"] = action_label
env.filters["move"] = move
env.filters["inr"] = report.inr
env.filters["status_label"] = status_label
env.filters["human"] = human
env.filters["reason"] = reason
env.filters["href"] = report.source_href
env.globals["css_v"] = lambda name="style.css": int((HERE / "static" / name).stat().st_mtime) \
    if (HERE / "static" / name).exists() else 0
env.globals["MOCK"] = USING_FAKE_DB or os.getenv("CRD_MOCK", "1") == "1"
env.filters["rating_label"] = report.rating_label
# Trust Issues voice for the UI only; reports and exports keep the formal rating names.
VERDICT = {"GREEN": "Trusted", "AMBER": "Doubts", "RED": "Don't trust", "CANNOT_VERIFY": "Can't tell",
           "HELD": "On hold"}
env.filters["verdict"] = lambda r: VERDICT.get(str(_val(r)), report.rating_label(r))
env.globals["VERDICT"] = VERDICT
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


FINISHED = ("done", "partial", "failed", "needs_clarification")


def _run_view(run: dict | None) -> dict | None:
    """Normalise a run row from either db: request text, as_of, n."""
    if not run:
        return run
    req = run.get("request")
    v = dict(run)
    if isinstance(req, dict):
        v["request_text"] = req.get("raw_text") or ""
        v["as_of"] = req.get("as_of") or run.get("as_of")
    else:
        v["request_text"] = req or ""
    if "n" not in v:
        try:
            v["n"] = len(db.get_assessments(run["run_id"]))
        except Exception:
            v["n"] = 0
    return v


def _cp_name(cp_id: str) -> str:
    getter = getattr(db, "get_counterparty", None)
    try:
        cp = getter(cp_id) if getter else None
    except Exception:
        cp = None
    return cp.name if cp else cp_id


def _progress_view(run_id: str) -> dict:
    """Normalise orchestrator.run_progress (real: flat step rows) into one row per counterparty."""
    p = orchestrator.run_progress(run_id)
    steps = p.get("steps") or []
    status = p.get("status") or (db.get_run(run_id) or {}).get("status") or "unknown"
    if steps and isinstance(steps[0], dict) and "step" in steps[0]:
        by_cp: dict[str, dict] = {}
        resolve_all = "pending"
        for s in steps:
            if s["cp_id"] == "__all__":
                if s["step"] == "resolve":
                    resolve_all = s["status"]
                continue
            row = by_cp.setdefault(s["cp_id"], {"cp_id": s["cp_id"], "display_name": _cp_name(s["cp_id"]),
                                                **{k: "pending" for k in STEPS}})
            row[s["step"]] = s["status"]
        groups = (db.get_step_output(run_id, "__all__", "resolve") or {}).get("groups", []) \
            if hasattr(db, "get_step_output") else []
        for g in groups:
            cid = (g.get("cp_ids") or [None])[0]
            if cid and cid not in by_cp:
                by_cp[cid] = {"cp_id": cid, "display_name": _cp_name(cid), **{k: "pending" for k in STEPS}}
        rows = list(by_cp.values())
        for r in rows:
            r["resolve"] = "done" if resolve_all == "done" else resolve_all
            if status in FINISHED:  # a step left 'running' by a stopped run is not spinning any more
                for k in STEPS:
                    if r[k] == "running":
                        r[k] = "error"
    else:
        rows = steps
    ledger = p.get("ledger") or p.get("recent_calls") or []
    total = p.get("total") or len(rows)
    return {"status": status, "done": p.get("done", 0), "total": total, "steps": rows,
            "ledger": ledger, "costs": p.get("costs") or db.run_costs(run_id), "note": p.get("note")}


def _costs_all() -> dict:
    rows = db.get_ledger(None)
    calls = len(rows)
    cached = sum(1 for r in rows if _val(r.status) == "cached")
    return {"credits": round(sum(r.credits for r in rows), 2), "inr": round(sum(r.inr for r in rows), 2),
            "calls": calls, "cached": cached, "cached_pct": round(100 * cached / calls, 1) if calls else 0.0,
            "errors": sum(1 for r in rows if _val(r.status) in ("error", "timeout")), "minutes": 0}


def _actions(statuses) -> list:
    out = []
    for s in statuses:
        out += db.get_actions(s)
    return out


# ---------------------------------------------------------------- front page / desk / runs
def _scale() -> dict:
    """Rating bands and risk probabilities, so the what-if line uses the same rules as scoring."""
    cfg = config.get()
    sc, rp = cfg.scoring, cfg.risk_probability
    return {"green_min": float(sc.get("green_min", 65)), "amber_min": float(sc.get("amber_min", 40)),
            "prob": {k: float(rp.get(k, 0.5)) for k in ("GREEN", "AMBER", "RED", "CANNOT_VERIFY", "HELD")}}


def _points(data: dict | None, run_id: str | None) -> list[dict]:
    """One JSON-safe point per counterparty for the risk line, desk and command palette."""
    out = []
    for r in (data or {}).get("rows", []):
        a = r["a"]
        out.append({"cp_id": r["cp_id"], "name": a.display_name, "role": human(_val(a.role)),
                    "rating": r["rating"], "score": a.score, "exposure": a.exposure_inr or 0,
                    "at_risk": a.inr_at_risk or 0, "prio": r["priority"], "importance": r["importance"],
                    "reason": reason(r["top_reason"]), "url": f"/cp/{run_id}/{r['cp_id']}"})
    return out


def _facts(data: dict | None, limit: int = 18) -> tuple[int, int, list[dict]]:
    """(facts, facts with a source link, a sample of sourced claims for the wire)."""
    evs = [e for v in (data or {}).get("evidence", {}).values() for e in v]
    sourced = [e for e in evs if report.source_href(e)]
    wire = [{"claim": e.claim, "source": e.source, "date": str(e.record_date or ""), "href": report.source_href(e)}
            for e in sourced if _val(e.status) == "ok"][:limit]
    return len(evs), len(sourced), wire


# Example situations for the front page, from the graded test set (tests/cases). The title is our framing;
# the description and source link come from the case's own notes. Shown as "what it's built to catch",
# never as this week's results.
EXAMPLES = [("04_lookalike", "Nine companies, one name"), ("03_insolvency", "Admitted to insolvency"),
            ("02_distressed_customer", "A customer winding down"), ("08_leadership_exit", "The CEO walked out"),
            ("07_false_alarm", "A false alarm"), ("09_namesake_news", "Same name, different company"),
            ("06_no_footprint", "No footprint at all"), ("10_source_failure", "A source went quiet")]


def _examples() -> list[dict]:
    import yaml

    out = []
    for cid, title in EXAMPLES:
        p = Path("tests") / "cases" / f"{cid}.yaml"
        try:
            case = yaml.safe_load(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            continue
        row = (case.get("input_rows") or [{}])[0]
        want = (case.get("expected") or {}).get("rating")
        verdict = (want[0] if isinstance(want, list) else want) or "CANNOT_VERIFY"
        notes = " ".join(str(case.get("notes") or "").split())
        sents = [s for s in re.split(r"(?<=[.!?])\s+", re.sub(r"Sources?:.*", "", notes)) if s and "http" not in s]
        first = sents[0] if sents else ""
        if cid == "10_source_failure":  # the case notes describe the test harness; say what the system does
            first = "A healthy company, but a data source times out mid-review. Silence is not health, so no green light."
        url = next((u.rstrip(".,)") for u in re.findall(r"https?://\S+", notes)), None)
        out.append({"title": title, "name": row.get("name", ""), "role": human(row.get("role", "")),
                    "verdict": verdict, "text": first if len(first) <= 190 else first[:189] + "…", "url": url})
    return out


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    run = _run_view(_latest_run())
    data = report.build(run["run_id"]) if run else None
    facts, sourced, wire = _facts(data)
    names = {"customer": None, "carrier": None}
    for r in (data or {}).get("rows", []):
        role = _val(r["a"].role)
        key = "customer" if role == "customer" else "carrier"
        names[key] = names[key] or r["a"].display_name
    return page(request, "landing.html", run=run, data=data, default_request=DEFAULT_REQUEST,
                counts=_counts(data["assessments"]) if data else None,
                points=_points(data, run and run["run_id"]), scale=_scale(),
                facts=facts, sourced=sourced, wire=wire, names=names, examples=_examples(),
                ledger=db.get_ledger(run["run_id"], limit=12) if run else [])


@app.get("/desk", response_class=HTMLResponse)
def dashboard(request: Request):
    run = _run_view(_latest_run())
    data = report.build(run["run_id"]) if run else None
    facts, sourced, _ = _facts(data, 0)
    rows = (data or {}).get("rows", [])
    kpi = {"exposure": sum(r["a"].exposure_inr or 0 for r in rows), "at_risk": sum(r["a"].inr_at_risk or 0 for r in rows),
           "n": len(rows), "attention": sum(1 for r in rows if r["rating"] != "GREEN"),
           "facts": facts, "sourced": sourced, "sourced_pct": round(100 * sourced / facts) if facts else 0}
    return page(request, "dashboard.html", run=run, data=data, default_request=DEFAULT_REQUEST,
                counts=_counts(data["assessments"]) if data else None, kpi=kpi,
                points=_points(data, run and run["run_id"]), scale=_scale())


# ---------------------------------------------------------------- sources: what Crustdata actually said
SOURCE_TYPES = ("crustdata_api", "registry")
# Rule 8 (personal data minimum): never show person-level endpoints, and withhold contact-type fields anywhere.
PERSONAL_KEY = re.compile(r"(e-?mail|phone|mobile|photo|picture|avatar|profile_pic|linkedin|twitter|facebook|"
                          r"instagram|personal|home_address|date_of_birth|dob)", re.I)
ENDPOINT_LABEL = {"identify": "Company Identify", "enrich": "Company Enrich", "job_search": "Job Search",
                  "web_search": "Web Search", "web_fetch": "Web Fetch", "registries": "Registries (IBBI / MCA / GST)"}


def _raw_roots() -> list[Path]:
    return [p.resolve() for p in (FIXTURES, Path("var") / "runs", VAR / "runs")]


def _raw_path(ref: str | None) -> Path | None:
    """Resolve an evidence raw_ref to a stored JSON file, only inside fixtures or run raw folders."""
    if not ref or ref.startswith("mock:") or "person_enrich" in ref:
        return None
    try:
        p = Path(ref).resolve()
    except (OSError, ValueError):
        return None
    if p.suffix != ".json" or not p.is_file():
        return None
    return p if any(root == p or root in p.parents for root in _raw_roots()) else None


def _raw_kind(p: Path) -> str:
    if FIXTURES.resolve() in p.parents:
        return "placeholder" if p.name.startswith("_default") else "fixture"
    return "live"


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("[withheld: personal data]" if PERSONAL_KEY.search(str(k)) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _endpoint_of(ref: str, source: str) -> str:
    parts = Path(ref).parts if ref and not ref.startswith("mock:") else ()
    for name in ENDPOINT_LABEL:
        if name in parts or Path(ref or "").name.startswith(name + "_") or (ref or "").startswith(f"mock:{name}"):
            return name
    return source


def _short(v, n: int = 60) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
    return s if len(s) <= n else s[: n - 1] + "…"


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request, run: str | None = None, cp: str | None = None, ref: str | None = None):
    run_row = _run_view(db.get_run(run) if run else _latest_run())
    companies = []
    if run_row:
        rid = run_row["run_id"]
        for a in db.get_assessments(rid):
            calls: dict[str, dict] = {}
            for cid in a.cp_ids:
                for e in db.get_evidence(rid, cid):
                    if _val(e.source_type) not in SOURCE_TYPES:
                        continue
                    key = e.raw_ref or f"none:{e.source}"
                    path = _raw_path(e.raw_ref)
                    c = calls.setdefault(key, {
                        "ref": e.raw_ref or "", "viewable": path is not None,
                        "kind": _raw_kind(path) if path else ("synthetic" if (e.raw_ref or "").startswith("mock:") else "missing"),
                        "endpoint": _endpoint_of(e.raw_ref or "", e.source), "source": e.source, "used": []})
                    c["used"].append({"signal": e.signal, "claim": e.claim, "path": e.field_path or "",
                                      "value": _short(e.value), "date": str(e.record_date or ""),
                                      "status": _val(e.status), "id": e.evidence_id})
            for c in calls.values():
                c["label"] = ENDPOINT_LABEL.get(c["endpoint"], c["source"])
            companies.append({"cp_id": a.cp_ids[0], "name": a.display_name, "rating": _val(a.rating),
                              "calls": sorted(calls.values(), key=lambda c: (not c["viewable"], c["label"]))})
        order = {"RED": 0, "AMBER": 1, "HELD": 2, "CANNOT_VERIFY": 3, "GREEN": 4}
        companies.sort(key=lambda c: (order.get(c["rating"], 5), c["name"]))
    led = [le for le in db.get_ledger(run_row["run_id"])] if run_row else []
    stats = {"calls": sum(1 for le in led if le.provider in ("crustdata", "registry")),
             "cached": sum(1 for le in led if le.provider in ("crustdata", "registry") and _val(le.status) == "cached"),
             "credits": round(sum(le.credits for le in led if le.provider == "crustdata"), 2)}
    return page(request, "sources.html", run=run_row, companies=companies, stats=stats, sel_cp=cp, sel_ref=ref,
                runs=db.list_runs())


@app.get("/api/source")
def api_source(ref: str, download: int = 0):
    p = _raw_path(ref)
    if not p:
        raise HTTPException(404, "no stored response for this reference")
    try:
        data = _redact(json.loads(p.read_text(encoding="utf-8")))
    except ValueError:
        raise HTTPException(422, "stored response is not valid JSON")
    if download:
        return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{p.name}"'})
    return {"ref": ref, "file": p.name, "kind": _raw_kind(p), "bytes": p.stat().st_size, "data": data}


@app.get("/api/points")
def api_points():
    """Latest run's counterparties, for the command palette on every page."""
    run = _latest_run()
    data = report.build(run["run_id"]) if run else None
    return {"run_id": run and run["run_id"], "points": _points(data, run and run["run_id"])}


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
    rows = [{**_run_view(r), "costs": db.run_costs(r["run_id"])} for r in db.list_runs()]
    return page(request, "runs.html", runs=rows)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_live(request: Request, run_id: str):
    run = _run_view(db.get_run(run_id))
    if not run:
        raise HTTPException(404, "run not found")
    return page(request, "run_live.html", run=run, prog=_progress_view(run_id))


@app.get("/runs/{run_id}/progress", response_class=HTMLResponse)
def run_progress(request: Request, run_id: str):
    run = _run_view(db.get_run(run_id)) or {"run_id": run_id, "status": "unknown"}
    return templates.TemplateResponse(request, "_progress.html", {"run": run, "prog": _progress_view(run_id)})


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
    from crd.actions import policy, priority

    cfg = config.get()
    cp = policy._counterparty(a.cp_ids[0])
    actions = [{"act": x, "ctx": policy.context(x, a, cp, cfg)}
               for x in db.get_actions(None, run_id) if x.cp_id in a.cp_ids]
    return page(request, "cp_detail.html", a=a, why=explain.why(run_id, cp_id) or {},
                prio=priority.for_assessment(a, cp, cfg=cfg),
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
    from crd.actions import policy

    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    cfg = config.get()
    pending = []
    for x in db.get_actions("pending_approval"):
        a = _assessment_for(x)
        pending.append({"act": x, "a": a, "reasons": _reasons(x), "ctx": policy.context(x, a, cfg=cfg)})
    pending.sort(key=lambda p: order.get(p["ctx"]["priority"] or "P4", 3))
    decided = [x for x in _actions(["approved", "executing", "done", "rejected", "failed"]) if not x.reversible]
    decided.sort(key=lambda x: str(x.executed_at or x.approved_at or ""), reverse=True)
    decided = decided[:10]
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
        from crd.actions import policy

        if policy.context(act)["requires_senior"]:
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
    return page(request, "ledger.html", rows=db.get_ledger(run), costs=db.run_costs(run) if run else _costs_all(),
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


FIXTURES = Path("tests") / "fixtures"


@app.get("/fixture/{rel:path}")
def fixture(rel: str):
    """Mock mode: evidence points at a recorded response in tests/fixtures."""
    root = FIXTURES.resolve()
    p = (FIXTURES / rel).resolve()
    if root not in p.parents or not p.is_file() or p.suffix != ".json":
        raise HTTPException(404, "fixture not found")
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
    import inspect

    if len(inspect.signature(db.save_watch_event).parameters) >= 3:
        fresh = db.save_watch_event(event_id, str(body.get("watch_id") or ""), body)
    else:
        fresh = db.save_watch_event(event_id, raw_body.decode("utf-8", "replace"))
    if not fresh:
        return {"ok": True, "duplicate": True}
    domains = watcher.parse_payload(body)
    for d in domains:
        _bg(orchestrator.run_single, d, "watcher")
    return {"ok": True, "queued": domains}


@app.get("/health")
def health():
    return {"ok": True, "mock": os.getenv("CRD_MOCK", "1") == "1", "fake_db": USING_FAKE_DB}
