"""Stand-in for crd/explain.py (Arth). Output shape follows the sample in section 11."""
from __future__ import annotations

from crd.fakes import db

REQUIRED = ["headcount_qoq", "headcount_6m", "hiring", "news"]


def why(run_id: str, cp_id: str) -> dict:
    a = db.get_assessment(run_id, cp_id)
    if not a:
        return {}
    ev = {e.evidence_id: e for cp in a.cp_ids for e in db.get_evidence(run_id, cp)}
    sigs = []
    for s in a.signals:
        sigs.append({
            "signal": s.signal, "points": s.points, "note": s.note,
            "evidence": [{"id": i, "claim": ev[i].claim, "url": ev[i].url, "raw_ref": ev[i].raw_ref,
                          "field_path": ev[i].field_path, "record_date": ev[i].record_date}
                         for i in s.evidence_ids if i in ev],
        })
    ok = sorted({e.signal for e in ev.values() if e.status in ("ok", "empty")} | {"identity"})
    return {
        "start": 70, "final_score": a.score, "rating": a.rating.value, "hard_stop": a.hard_stop,
        "signals": sigs,
        "coverage": {"ok_signals": ok, "required": REQUIRED,
                     "passed": a.rating.value not in ("CANNOT_VERIFY", "HELD")},
        "unavailable": [e.claim for e in ev.values() if e.status == "unavailable"],
        "conflicts": a.conflicts,
    }
