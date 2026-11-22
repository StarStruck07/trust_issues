"""Identity resolution + dedupe. Owner: Arth (P1).

Order: domain -> CIN/GSTIN -> name. A name match is accepted only when it is unique and
(if the record has a city/state) the location agrees. Otherwise: ambiguous / not_found.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from crd.data import registries
from crd.data.errors import BudgetExceeded, CreditsExhausted, SourceUnavailable
from crd.models import Counterparty, Evidence, ResolvedEntity

SUFFIXES = r"\b(private limited|pvt\.? ltd\.?|pvt|private|limited|ltd\.?|llp|inc\.?|india|co\.?|company|corporation|corp\.?)\b"


def norm(name: str | None) -> str:
    s = (name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(SUFFIXES, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def pan_of(gstin: str | None) -> str | None:
    g = (gstin or "").strip().upper()
    return g[2:12] if len(g) == 15 else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _matches(resp: Any) -> list[dict]:
    """Flatten Crustdata identify responses into [{confidence, company_id, name, domain, location}]."""
    out: list[dict] = []
    items = resp if isinstance(resp, list) else (resp or {}).get("results") or [resp]
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for m in item.get("matches", []) or []:
            cd = m.get("company_data", {}) or {}
            bi = cd.get("basic_info", {}) or {}
            loc = cd.get("locations") or bi.get("location") or bi.get("headquarters") or ""
            out.append({
                "confidence": float(m.get("confidence_score") or 0),
                "crustdata_company_id": cd.get("crustdata_company_id") or bi.get("crustdata_company_id"),
                "name": bi.get("name") or cd.get("name"),
                "domain": bi.get("primary_domain") or bi.get("domain") or (bi.get("domains") or [None])[0],
                "location": loc if isinstance(loc, str) else str(loc),
            })
    return sorted(out, key=lambda m: -m["confidence"])


def _ev(run_id: str, cp_id: str, claim: str, value: Any, raw_ref: str | None, status: str = "ok",
        field_path: str = "matches[0].confidence_score") -> Evidence:
    return Evidence(
        evidence_id=f"ev_{uuid.uuid4().hex[:8]}", run_id=run_id, cp_id=cp_id, signal="identity",
        claim=claim, value=value, status=status, source_type="crustdata_api",
        source="POST /company/identify", field_path=field_path, raw_ref=raw_ref, retrieved_at=_now(),
    )


def _entity(cp: Counterparty, m: dict, match_type: str) -> ResolvedEntity:
    return ResolvedEntity(
        cp_ids=[cp.cp_id], status="resolved", crustdata_company_id=m.get("crustdata_company_id"),
        legal_name=m.get("name") or cp.legal_name or cp.name, domain=m.get("domain") or cp.domain,
        match_type=match_type, confidence=m["confidence"],
    )


def resolve_one(cp: Counterparty, client, run_id: str, cfg) -> tuple[ResolvedEntity, list[Evidence]]:
    min_conf = float(cfg.identity.min_confidence)
    tried: list[str] = []
    try:
        # 1. domain
        if cp.domain:
            ms = _matches(client.identify(domains=[cp.domain]))
            tried.append(f"Company Identify by domain ({len(ms)} matches)")
            if ms and ms[0]["confidence"] >= 0.99:
                e = _entity(cp, ms[0], "domain")
                ev = _ev(run_id, cp.cp_id, f"Resolved to {e.legal_name} by domain {cp.domain} "
                         f"(confidence {ms[0]['confidence']:.2f})", ms[0], client.last_raw_ref)
                e.evidence_ids = [ev.evidence_id]
                return e, [ev]
        # 2. CIN / GSTIN via registry
        if cp.cin or cp.gstin:
            reg = registries.lookup_by_id(cin=cp.cin, gstin=cp.gstin, client=client)
            if reg and reg.get("legal_name"):
                ms = _matches(client.identify(names=[reg["legal_name"]]))
                tried.append(f"Registry lookup by {'CIN' if cp.cin else 'GSTIN'} + Company Identify")
                same = [m for m in ms if norm(m["name"]) == norm(reg["legal_name"])]
                if len(same) == 1:
                    mt = "cin" if cp.cin else "gstin"
                    e = _entity(cp, same[0], mt)
                    ev = _ev(run_id, cp.cp_id, f"Resolved to {e.legal_name} via {mt.upper()} registry name",
                             same[0], client.last_raw_ref)
                    e.evidence_ids = [ev.evidence_id]
                    return e, [ev]
        # 3. name
        qname = cp.legal_name or cp.name
        ms = _matches(client.identify(names=[qname]))
        tried.append(f"Company Identify by name '{qname}' ({len(ms)} matches)")
        good = [m for m in ms if m["confidence"] >= min_conf]
        loc = " ".join(x for x in (cp.city, cp.state) if x).lower()
        if loc and len(good) > 1:
            located = [m for m in good if any(t in (m.get("location") or "").lower() for t in loc.split())]
            if len(located) == 1:
                good = located
        if len(good) == 1 and (not loc or not good[0].get("location")
                               or any(t in good[0]["location"].lower() for t in loc.split())):
            e = _entity(cp, good[0], "name")
            ev = _ev(run_id, cp.cp_id, f"Resolved '{qname}' to {e.legal_name} by unique name match "
                     f"(confidence {good[0]['confidence']:.2f})", good[0], client.last_raw_ref)
            e.evidence_ids = [ev.evidence_id]
            return e, [ev]
        if len(good) >= 2:
            cands = [{k: m.get(k) for k in ("name", "domain", "location", "confidence", "crustdata_company_id")}
                     for m in good[:5]]
            ev = _ev(run_id, cp.cp_id, f"'{qname}' matches {len(good)} companies at confidence >= {min_conf}",
                     cands, client.last_raw_ref, status="empty")
            return ResolvedEntity(cp_ids=[cp.cp_id], status="ambiguous", candidates=cands,
                                  legal_name=cp.legal_name, domain=cp.domain,
                                  evidence_ids=[ev.evidence_id]), [ev]
        ev = _ev(run_id, cp.cp_id, f"No company record found for '{qname}'. Tried: " + "; ".join(tried),
                 {"tried": tried}, client.last_raw_ref, status="empty")
        return ResolvedEntity(cp_ids=[cp.cp_id], status="not_found", legal_name=cp.legal_name,
                              domain=cp.domain, evidence_ids=[ev.evidence_id]), [ev]
    except (BudgetExceeded, CreditsExhausted):
        raise
    except (SourceUnavailable, NotImplementedError, Exception) as exc:  # source failed: HELD, not "not found"
        ev = _ev(run_id, cp.cp_id, f"Company Identify unavailable: {str(exc)[:120]}", None,
                 getattr(client, "last_raw_ref", None), status="unavailable")
        return ResolvedEntity(cp_ids=[cp.cp_id], status="not_found", legal_name=cp.legal_name,
                              domain=cp.domain, evidence_ids=[ev.evidence_id]), [ev]


CRIT_ORDER = {"sole_source": 3, "primary": 2, "backup": 1, "na": 0}


def dedupe(items: list[tuple[ResolvedEntity, Counterparty, list[Evidence]]]
           ) -> list[tuple[ResolvedEntity, list[Counterparty], list[Evidence]]]:
    """Merge rows that are the same company (company id, CIN, PAN, domain). Never merge on name alone."""
    groups: list[dict] = []
    for ent, cp, ev in items:
        keys = set()
        if ent.status == "resolved" and ent.crustdata_company_id:
            keys.add(f"id:{ent.crustdata_company_id}")
        if cp.cin:
            keys.add(f"cin:{cp.cin}")
        if pan_of(cp.gstin):
            keys.add(f"pan:{pan_of(cp.gstin)}")
        d = (ent.domain if ent.status == "resolved" else None) or cp.domain
        if d:
            keys.add(f"dom:{d.lower()}")
        target = next((g for g in groups if g["keys"] & keys), None) if keys else None
        if target:
            target["keys"] |= keys
            target["cps"].append(cp)
            target["ev"].extend(ev)
            if target["ent"].status != "resolved" and ent.status == "resolved":
                target["ent"] = ent
        else:
            groups.append({"keys": keys, "ent": ent, "cps": [cp], "ev": list(ev)})
    out = []
    for g in groups:
        cps = sorted(g["cps"], key=lambda c: c.cp_id)
        ent = g["ent"].model_copy(update={
            "cp_ids": [c.cp_id for c in cps],
            "evidence_ids": [e.evidence_id for e in g["ev"]],
        })
        for e in g["ev"]:
            e.cp_id = cps[0].cp_id
        out.append((ent, cps, g["ev"]))
    return out


def resolve_all(cps: list[Counterparty], client, run_id: str, cfg
                ) -> list[tuple[ResolvedEntity, list[Counterparty], list[Evidence]]]:
    items = []
    for cp in cps:
        ent, ev = resolve_one(cp, client, run_id, cfg)
        items.append((ent, cp, ev))
    return dedupe(items)
