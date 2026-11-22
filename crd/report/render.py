"""Review outputs: review.json / review.md / review.html / ledger.csv (section 8)."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from crd.deps import config, db

HERE = Path(__file__).parent
STATIC_CSS = HERE.parent / "web" / "static" / "style.css"
RATING_LABEL = {"CANNOT_VERIFY": "CANNOT VERIFY"}
FOOTER = ("Ratings computed by fixed rules from cited evidence; "
          "the model only extracts facts and writes summaries.")


def run_dir(run_id: str) -> Path:
    return Path(os.getenv("CRD_VAR", "var")) / "runs" / run_id


# ---------------------------------------------------------------- formatting helpers
def inr(v) -> str:
    """Indian units: ₹42.0 L, ₹1.2 Cr, ₹85,000."""
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:.1f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.1f} L"
    s = f"{int(round(v)):d}"
    neg = s.startswith("-")
    s = s.lstrip("-")
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"{'-' if neg else ''}₹{s}"


def rating_label(r) -> str:
    r = getattr(r, "value", r)
    return RATING_LABEL.get(r, r)


def _val(r):
    return getattr(r, "value", r)


# ---------------------------------------------------------------- data assembly
def top_reason(a) -> str:
    neg = sorted(a.signals, key=lambda s: s.points)
    if a.hard_stop:
        return f"hard stop: {a.hard_stop}"
    if neg and neg[0].points < 0:
        return neg[0].note
    if a.cannot_verify:
        return a.cannot_verify[0]
    return "no negative signals"


def top_sources(a, evidence: dict) -> list[dict]:
    out = []
    for s in sorted(a.signals, key=lambda s: s.points):
        for eid in s.evidence_ids:
            e = evidence.get(eid)
            if e and len(out) < 2:
                out.append({"id": eid, "url": source_href(e), "label": e.source or e.signal,
                            "record_date": e.record_date})
    return out


def source_href(e) -> str | None:
    if getattr(e, "url", None):
        return e.url
    ref = getattr(e, "raw_ref", None)
    if not ref:
        return None
    ref = str(ref).replace("\\", "/")
    if "tests/fixtures/" in ref:
        return "/fixture/" + ref.split("tests/fixtures/", 1)[1]
    name = ref.rsplit("/", 1)[-1]
    run = ref.split("/runs/", 1)[1].split("/", 1)[0] if "/runs/" in ref else e.run_id
    return f"/raw/{run}/{name}"


def field_value(a, name: str, ctx: dict):
    """Unknown field names: assessment.extra first, then top-level attributes."""
    if name in ctx:
        return ctx[name]
    if name in (a.extra or {}):
        return a.extra[name]
    return getattr(a, name, "")


def report_fields() -> list[str]:
    rep = getattr(config.get(), "report", None)
    fields = getattr(rep, "fields", None) if rep is not None else None
    if fields is None and isinstance(rep, dict):
        fields = rep.get("fields")
    return list(fields or ["display_name", "role", "rating", "score", "inr_at_risk", "top_reason",
                           "sources", "action_status"])


def action_status(a, actions_by_cp: dict) -> str:
    acts = [x for cp in a.cp_ids for x in actions_by_cp.get(cp, []) if not x.reversible]
    if not acts:
        return "—"
    return ", ".join(sorted({_val(x.status) for x in acts}))


def build(run_id: str) -> dict:
    run = dict(db.get_run(run_id) or {"run_id": run_id, "status": "unknown"})
    req = run.get("request")
    if isinstance(req, dict):  # real db: request is the ReviewRequest dict
        run["request"] = req.get("raw_text") or ""
        run["as_of"] = req.get("as_of") or run.get("as_of")
    from crd.actions import priority

    cfg = config.get()
    prio = {}
    for a in db.get_assessments(run_id):
        prio[a.cp_ids[0]] = priority.for_assessment(a, cfg=cfg)
    # most urgent first, then most ₹ at risk
    assessments = sorted(db.get_assessments(run_id),
                         key=lambda a: (prio[a.cp_ids[0]]["tier"], -(a.inr_at_risk or 0)))
    actions = db.get_actions(None, run_id)
    by_cp: dict[str, list] = {}
    for x in actions:
        by_cp.setdefault(x.cp_id, []).append(x)
    evidence: dict[str, list] = {}
    ev_by_id = {}
    for a in assessments:
        for cp in a.cp_ids:
            evs = db.get_evidence(run_id, cp)
            evidence[a.cp_ids[0]] = evidence.get(a.cp_ids[0], []) + evs
            ev_by_id.update({e.evidence_id: e for e in evs})
    fields = report_fields()
    rows = []
    for a in assessments:
        ctx = {
            "top_reason": top_reason(a),
            "sources": top_sources(a, ev_by_id),
            "action_status": action_status(a, by_cp),
            "rating": _val(a.rating),
            "cp_id": a.cp_ids[0],
            "priority": prio[a.cp_ids[0]]["tier"],
            "prio": prio[a.cp_ids[0]],
            "importance": prio[a.cp_ids[0]]["importance"],
        }
        cells = {f: field_value(a, f, ctx) for f in fields}
        cells.setdefault("priority", ctx["priority"])
        rows.append({"a": a, "cells": cells, **ctx})
    costs = db.run_costs(run_id)
    return {
        "run": run, "request": run.get("request"), "as_of": run.get("as_of"), "costs": costs,
        "fields": fields, "rows": rows,
        # the dashboard always leads with priority; exported reports follow config exactly
        "ui_fields": fields if "priority" in fields else ["priority"] + fields, "assessments": assessments, "actions": actions,
        "evidence": evidence, "ev_by_id": ev_by_id,
        "changes": [a for a in assessments if a.change_since_last],
        "cannot_verify": [(a, line) for a in assessments for line in a.cannot_verify],
        "pending": [x for x in actions if _val(x.status) == "pending_approval"],
        "row_errors": (run.get("doc") or {}).get("row_errors", []),
        "not_reviewed": (run.get("doc") or {}).get("not_reviewed", []) or ([run["note"]] if run.get("note") else []),
        "footer": FOOTER,
        "prio": prio,
        "matrix": _matrix(assessments, prio),
    }


def _matrix(assessments, prio) -> dict:
    """importance (rows) × rating group (cols) -> list of (cp_id, display_name)."""
    cols = ["RED", "AMBER", "CV", "GREEN"]
    m = {imp: {c: [] for c in cols} for imp in ("strategic", "core", "standard")}
    for a in assessments:
        r = _val(a.rating)
        col = "CV" if r in ("CANNOT_VERIFY", "HELD") else r
        p = prio[a.cp_ids[0]]
        m[p["importance"]][col].append({"cp_id": a.cp_ids[0], "name": a.display_name, "tier": p["tier"]})
    return m


# ---------------------------------------------------------------- outputs
def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(HERE / "templates")),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["inr"] = inr
    env.filters["rating_label"] = rating_label
    env.filters["val"] = _val
    return env


def to_markdown(d: dict) -> str:
    run, c = d["run"], d["costs"]
    L = [f"# Counterparty risk review — {run.get('run_id')}", "",
         f"- **As of:** {d['as_of']}", f"- **Request:** {d['request']}",
         f"- **Counterparties reviewed:** {len(d['assessments'])}",
         f"- **Cost:** {c['credits']} credits · {inr(c['inr'])} · {c['minutes']} min",
         f"- **Status:** {run.get('status')}", ""]
    if d["not_reviewed"]:
        L += ["**Not reviewed:**"] + [f"- {x}" for x in d["not_reviewed"]] + [""]
    L += ["## Changes since the last review", ""]
    L += [f"- {a.display_name}: {a.change_since_last}" for a in d["changes"]] or ["- none"]
    L += ["", "## Ranked by priority, then ₹ at risk", "",
          "_Priority = rating × how much the relationship matters (P1 act now · P2 this week · P3 monitor · P4 no action)._", "", "| " + " | ".join(d["fields"]) + " |",
          "|" + "---|" * len(d["fields"])]
    for r in d["rows"]:
        cells = []
        for f in d["fields"]:
            v = r["cells"][f]
            if f == "sources":
                v = " ".join(f"[{s['label']}]({s['url']})" if s["url"] else s["label"] for s in v)
            elif f in ("inr_at_risk", "exposure_inr"):
                v = inr(v)
            elif f == "rating":
                v = rating_label(v)
            cells.append(str(v).replace("|", "\\|"))
        L.append("| " + " | ".join(cells) + " |")
    L += ["", "## Cannot verify / Held", ""]
    L += [f"- {line}" for _, line in d["cannot_verify"]] or ["- none"]
    L += ["", "## Approval queue", ""]
    L += [f"- **{x.subject}** ({_val(x.type)}, {x.cp_id}) — evidence: {', '.join(x.reason_evidence)}"
          for x in d["pending"]] or ["- nothing pending"]
    L += ["", "## Counterparties", ""]
    for a in d["assessments"]:
        L += [f"### {a.display_name} — {rating_label(a.rating)} ({a.score}/100)", "", a.explanation, ""]
        for s in a.signals:
            L.append(f"- **{s.signal}** {s.points:+d}: {s.note}")
            for eid in s.evidence_ids:
                e = d["ev_by_id"].get(eid)
                if e:
                    href = source_href(e)
                    src = f"[{e.source or 'source'}]({href})" if href else (e.source or "")
                    L.append(f"  - {e.claim} — {src} · {e.record_date or 'n/a'}{' · STALE' if e.stale else ''}")
        L.append("")
    L += ["---", d["footer"]]
    return "\n".join(L)


def render(run_id: str) -> dict:
    out = run_dir(run_id)
    out.mkdir(parents=True, exist_ok=True)
    d = build(run_id)

    j = {
        "run": d["run"], "request": d["request"], "as_of": d["as_of"], "costs": d["costs"],
        "assessments": [a.model_dump(mode="json") for a in d["assessments"]],
        "actions": [x.model_dump(mode="json") for x in d["actions"]],
        "evidence": {cp: [e.model_dump(mode="json") for e in evs] for cp, evs in d["evidence"].items()},
        "row_errors": d["row_errors"],
    }
    (out / "review.json").write_text(json.dumps(j, indent=2, default=str), encoding="utf-8")
    (out / "review.md").write_text(to_markdown(d), encoding="utf-8")
    css = STATIC_CSS.read_text(encoding="utf-8") if STATIC_CSS.exists() else ""
    html = _env().get_template("report.html.j2").render(d=d, css=css, inr=inr, source_href=source_href)
    (out / "review.html").write_text(html, encoding="utf-8")

    ledger = db.get_ledger(run_id)
    with open(out / "ledger.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        cols = ["ts", "provider", "endpoint", "credits", "inr", "tokens_in", "tokens_out",
                "latency_ms", "status", "note", "call_id", "request_hash"]
        w.writerow(cols)
        for le in ledger:
            w.writerow([getattr(le, k, "") for k in cols])
    return {"json": str(out / "review.json"), "md": str(out / "review.md"),
            "html": str(out / "review.html"), "ledger_csv": str(out / "ledger.csv")}
