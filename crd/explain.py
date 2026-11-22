"""Plain-language explanations + score breakdown. Owner: Arth (P1).

The LLM may only rephrase cited facts. A checker rejects any number/date/evidence id not present in
the facts; on failure (or no LLM) a deterministic template is used.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from crd import config, db
from crd.models import Assessment, Evidence, Rating


class _Text(BaseModel):
    text: str


SYSTEM = (
    "You write two short, plain-English sentences explaining a supplier/customer risk rating to a credit manager. "
    "Use ONLY the facts given. After each fact write its evidence id in square brackets, e.g. [ev_1a2b3c4d]. "
    "Do not add numbers, dates or claims that are not in the facts. No speculation."
)


def _facts(a: Assessment, ev: list[Evidence]) -> list[dict]:
    by_id = {e.evidence_id: e for e in ev}
    out = []
    for s in sorted(a.signals, key=lambda s: (s.points, s.signal))[:3]:
        for eid in s.evidence_ids[:1]:
            e = by_id.get(eid)
            if e:
                out.append({"id": eid, "claim": e.claim, "date": str(e.record_date or ""), "points": s.points})
    return out


def _check(text: str, facts: list[dict], a: Assessment) -> bool:
    allowed = " ".join(f"{f['claim']} {f['date']} {f['points']}" for f in facts) + f" {a.score} {a.rating.value}"
    for n in re.findall(r"\d[\d,.]*", re.sub(r"\[ev_[0-9a-f]+\]", "", text)):
        n = n.rstrip(".,")
        if n and n not in allowed and n.replace(",", "") not in allowed.replace(",", ""):
            return False
    ids = set(re.findall(r"ev_[0-9a-f]+", text))
    return ids <= {f["id"] for f in facts}


def template(a: Assessment, facts: list[dict]) -> str:
    if a.rating in (Rating.CANNOT_VERIFY, Rating.HELD) and a.cannot_verify:
        return a.cannot_verify[0]
    if a.hard_stop:
        f = facts[0] if facts else None
        return f"RED (hard stop: {a.hard_stop}). " + (f"{f['claim']} [{f['id']}]." if f else "")
    neg = [f for f in facts if f["points"] < 0]
    if not neg:
        return f"{a.rating.value}: no material negative signal in fresh, sourced evidence (score {a.score}/100)."
    parts = "; ".join(f"{f['claim']} [{f['id']}]" for f in neg[:2])
    return f"{a.rating.value} (score {a.score}/100): {parts}."


def write(a: Assessment, ev: list[Evidence], run_id: str) -> str:
    facts = _facts(a, ev)
    if a.rating in (Rating.CANNOT_VERIFY, Rating.HELD) or a.hard_stop:
        return template(a, facts)
    try:
        from crd import llm
        cfg = config.get()
        user = f"Company: {a.display_name}\nRating: {a.rating.value} (score {a.score}/100)\nFacts:\n" + "\n".join(
            f"- {f['claim']} (record date {f['date'] or 'n/a'}) [{f['id']}]" for f in facts)
        out = llm.complete_json(SYSTEM, user, _Text, run_id, model=cfg.get("llm", {}).get("model"))
        text = out.text.strip()
        if text and _check(text, facts, a):
            return text
    except Exception:
        pass
    return template(a, facts)


def why(run_id: str, cp_id: str) -> dict:
    a = db.get_assessment(run_id, cp_id)
    if not a:
        return {}
    cfg = config.get()
    ev = db.get_evidence(run_id, a.cp_ids[0])
    by_id = {e.evidence_id: e for e in ev}
    ok = {e.signal for e in ev if e.status == "ok" and not e.stale}
    required = list(cfg.scoring.coverage_required_any)
    return {
        "start": cfg.scoring.start,
        "final_score": a.score,
        "rating": a.rating.value,
        "hard_stop": a.hard_stop,
        "signals": [
            {"signal": s.signal, "points": s.points, "note": s.note,
             "evidence": [{"id": i, "claim": by_id[i].claim, "url": by_id[i].url, "field_path": by_id[i].field_path,
                           "record_date": str(by_id[i].record_date or ""), "source": by_id[i].source}
                          for i in s.evidence_ids if i in by_id]}
            for s in a.signals
        ],
        "coverage": {"ok_signals": sorted(ok), "required": required,
                     "min_signals": cfg.scoring.coverage_min_signals,
                     "passed": len(ok) >= cfg.scoring.coverage_min_signals and bool(ok & set(required))},
        "unavailable": [e.claim for e in ev if e.status == "unavailable"],
        "stale": [e.claim for e in ev if e.stale],
        "conflicts": a.conflicts,
        "cannot_verify": a.cannot_verify,
        "explanation": a.explanation,
    }
