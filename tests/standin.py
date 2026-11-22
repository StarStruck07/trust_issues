"""Test-only stand-ins (Arth) so the core pipeline runs on Nilay's REAL recorded responses before his
client/signals land. Not used in production: crd/data/* stays Nilay's. Mapping follows his fixtures README."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone

from crd.models import Evidence

FIX = "tests/fixtures"
KEYWORDS = [  # rule fallback categories (same idea as NILAY_data.md section 8.6)
    (("insolvency", "nclt", "cirp", "liquidation"), "hard_stop"),
    (("raid", "fraud", "gst evasion"), "fraud_or_raid"),
    (("layoff", "lays off", "retrench", "shut down", "shutter"), "layoffs_or_shutdown"),
    (("strike", "fire at", "flood", "halt"), "operations_halt"),
    (("acquire", "merger", "demerger"), "irrelevant"),
]


def _load(endpoint: str, key: str):
    p = os.path.join(FIX, endpoint, f"{key.lower().replace(' ', '-')}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


class FixtureClient:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.last_raw_ref = None

    def identify(self, *, domains=None, names=None):
        out = []
        for k in (domains or names):
            d = _load("identify", k)
            self.last_raw_ref = f"{FIX}/identify/{k}.json"
            out.extend(d if d else [{"matched_on": k, "matches": []}])
        return out


def _ev(run_id, cp_id, signal, claim, value, field_path, record_date, **kw):
    return Evidence(evidence_id=f"ev_{uuid.uuid4().hex[:8]}", run_id=run_id, cp_id=cp_id, signal=signal,
                    claim=claim, value=value, source_type=kw.pop("source_type", "crustdata_api"),
                    source=kw.pop("source", "POST /company/enrich"), field_path=field_path,
                    record_date=record_date, retrieved_at=datetime.now(timezone.utc), **kw)


def gather(cp, entity, client, cfg):
    raw = _load("enrich", entity.domain or "")
    if not raw:
        return [_ev(client.run_id, cp.cp_id, "headcount_qoq", "enrich unavailable: no fixture", None,
                    None, None, status="unavailable")]
    cd = raw[0]["matches"][0]["company_data"]
    ref = f"{FIX}/enrich/{entity.domain}.json"
    hc = cd.get("headcount") or {}
    ts = hc.get("timeseries") or []
    rdate = date.fromisoformat(ts[-1]["date"]) if ts else None
    g = hc.get("growth_percent") or {}
    rid, cid = client.run_id, cp.cp_id
    out = []
    if g.get("qoq") is not None:
        out.append(_ev(rid, cid, "headcount_qoq", f"Headcount changed {g['qoq']:+.1f}% over the last quarter "
                       f"(total {hc.get('total')})", g["qoq"], "headcount.growth_percent.qoq", rdate, raw_ref=ref))
    if g.get("six_months") is not None:
        out.append(_ev(rid, cid, "headcount_6m", f"Headcount changed {g['six_months']:+.1f}% over six months",
                       g["six_months"], "headcount.growth_percent.six_months", rdate, raw_ref=ref))
    r = ((cd.get("employee_reviews") or {}).get("overall_rating") or {})
    if r.get("rating") is not None:
        out.append(_ev(rid, cid, "employee_reviews", f"Employee rating {r['rating']}/5 from {r.get('total_count')} "
                       f"reviews", {"rating": r["rating"], "count": r.get("total_count", 0)},
                       "employee_reviews.overall_rating.rating", rdate, raw_ref=ref))
    for n in (cd.get("news") or [])[:15]:
        t = (n.get("article_title") or "").lower()
        cat = next((c for keys, c in KEYWORDS if any(k in t for k in keys)), "minor_none")
        if cat == "minor_none":
            continue
        out.append(_ev(rid, cid, "news", n.get("article_title"), {"category": cat, "relevant": True,
                       "subtype": "acquisition" if cat == "irrelevant" else None},
                       None, date.fromisoformat(n["article_publish_date"]), source_type="web",
                       source="Enrich news[]", url=n.get("article_url"), passage=n.get("article_title")[:80]))
    jobs = _load("job_search", entity.domain or "")
    if jobs is not None:
        out.append(_ev(rid, cid, "hiring", "Job postings present in Job Search", {"recent": 5, "prior": 5,
                       "distress_roles": []}, "job_search.total_count", rdate, source="POST /job/search"))
    return out
