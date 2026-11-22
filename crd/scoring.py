"""Deterministic scoring. Owner: Arth (P1). Pure function: no DB, no network, no LLM.

The rating is computed only from cited Evidence + config.yaml. Same inputs (in any order) -> same output.
"""
from __future__ import annotations

from typing import Any, Optional

from crd.models import (
    Assessment, Counterparty, Evidence, Rating, ResolvedEntity, Role, SignalScore, SUPPLIER_ROLES,
)

NEUTRAL_SUBTYPES = ("acquisition", "acquired", "merger", "demerger", "amalgamation", "carve")


def _v(e: Evidence, key: str, default: Any = None) -> Any:
    return e.value.get(key, default) if isinstance(e.value, dict) else default


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _threshold_points(value: float, table: list) -> tuple[int, str]:
    for thr, pts in table:
        if value <= thr:
            return int(pts), f"{value:+.1f}% <= {thr}% -> {pts}"
    return 0, f"{value:+.1f}% above thresholds -> 0"


def _sources_tried(evidence: list[Evidence]) -> str:
    seen = []
    for e in evidence:
        s = f"{e.source} ({e.status})"
        if s not in seen:
            seen.append(s)
    return ", ".join(seen) or "none"


def score(cps: list[Counterparty], entity: ResolvedEntity, evidence: list[Evidence], cfg, run_id: str,
          previous: Optional[str] = None) -> Assessment:
    sc = cfg.scoring
    pts_cfg = sc.points
    primary = sorted(cps, key=lambda c: c.cp_id)[0]
    name = entity.legal_name or primary.legal_name or primary.name
    exposure = float(sum(c.exposure_inr or 0 for c in cps))
    evidence = sorted(evidence, key=lambda e: (e.signal, e.evidence_id))  # determinism
    ok = [e for e in evidence if e.status == "ok" and not e.stale]
    unavailable = [e for e in evidence if e.status == "unavailable"]

    signals: list[SignalScore] = []
    cannot_verify: list[str] = []
    conflicts: list[str] = []
    hard_stop: Optional[str] = None
    rating: Optional[Rating] = None

    def finish(r: Rating, s: int) -> Assessment:
        prob = float(cfg.risk_probability.get(r.value, 0.5))
        top = sorted([x for x in signals if x.points < 0], key=lambda x: x.points)
        return Assessment(
            run_id=run_id, cp_ids=[c.cp_id for c in sorted(cps, key=lambda c: c.cp_id)], display_name=name,
            role=primary.role, entity=entity, rating=r, score=int(s), hard_stop=hard_stop, signals=signals,
            cannot_verify=cannot_verify, conflicts=conflicts,
            change_since_last=(f"{previous} -> {r.value}" if previous and previous != r.value else None),
            exposure_inr=exposure, inr_at_risk=round(exposure * prob, 2),
            extra={"top_reason": top[0].note if top else (cannot_verify[0] if cannot_verify else ""),
                   "unavailable": [e.claim for e in unavailable]},
        )

    # 1. identity gate --------------------------------------------------------------
    id_ev = [e for e in evidence if e.signal == "identity"]
    if entity.status == "ambiguous":
        cannot_verify.append(
            f"CANNOT VERIFY: which company is '{primary.name}'? Missing: a unique match (domain, CIN or GSTIN). "
            f"Tried: Company Identify ({len(entity.candidates)} candidates at confidence >= "
            f"{cfg.identity.min_confidence}).")
        signals.append(SignalScore(signal="identity", points=0, evidence_ids=[e.evidence_id for e in id_ev],
                                   note="ambiguous identity"))
        return finish(Rating.CANNOT_VERIFY, 0)
    if entity.status == "not_found":
        # No company record, but the registry was searched with the exact legal name from the client's
        # own list: an insolvency notice for that name is still a valid hard stop.
        hs = {str(x).lower() for x in sc.hard_stops}
        for e in ok:
            val = str(e.value if not isinstance(e.value, dict) else e.value.get("status", "")).lower()
            if e.signal == "insolvency" and e.source_type == "registry" and val in hs:
                hard_stop = val
                signals.append(SignalScore(signal="insolvency", points=-100, evidence_ids=[e.evidence_id],
                                           note=f"hard stop: {val} (registry, by legal name)"))
                conflicts.append(f"No company record matched '{primary.legal_name or primary.name}'; the "
                                 "registry notice was found by the exact legal name from our list.")
                return finish(Rating.RED, 0)
        if any(e.status == "unavailable" for e in id_ev):
            cannot_verify.append(
                f"HELD: is '{primary.name}' a real, active company? Missing: identity check (source unavailable). "
                f"Tried: {_sources_tried(id_ev)}.")
            return finish(Rating.HELD, 0)
        cannot_verify.append(
            f"CANNOT VERIFY: is '{primary.name}' a real, trading company? Missing: any company record matching "
            f"this name/domain. Tried: {_sources_tried(id_ev)}.")
        return finish(Rating.CANNOT_VERIFY, 0)

    # 2. hard stops -------------------------------------------------------------------
    hs_values = {str(x).lower() for x in sc.hard_stops}
    for e in ok:
        val = str(e.value if not isinstance(e.value, dict) else e.value.get("status", "")).lower()
        if e.signal in ("legal_status", "insolvency") and val in hs_values:
            hard_stop = val
            signals.append(SignalScore(signal=e.signal, points=-100, evidence_ids=[e.evidence_id],
                                       note=f"hard stop: {val}"))
            break
        if e.signal == "news" and _v(e, "relevant", True) and _v(e, "category") == "hard_stop":
            hard_stop = "insolvency_news"
            signals.append(SignalScore(signal="news", points=-100, evidence_ids=[e.evidence_id],
                                       note="hard stop: insolvency/default reported"))
            break
    if hard_stop:
        return finish(Rating.RED, 0)

    # 3. points -----------------------------------------------------------------------
    total = int(sc.start)
    by_sig: dict[str, list[Evidence]] = {}
    for e in ok:
        by_sig.setdefault(e.signal, []).append(e)

    def add(signal: str, points: int, evs: list[Evidence], note: str) -> None:
        signals.append(SignalScore(signal=signal, points=int(points),
                                   evidence_ids=[e.evidence_id for e in evs], note=note))

    # false-alarm detection first (acquisition / merger news neutralises headcount drops)
    neutral_news = [e for e in by_sig.get("news", [])
                    if any(t in str(_v(e, "subtype", "")).lower() for t in NEUTRAL_SUBTYPES)]

    for sig in ("headcount_qoq", "headcount_6m"):
        for e in by_sig.get(sig, [])[:1]:
            v = _num(e.value if not isinstance(e.value, dict) else _v(e, "pct"))
            if v is None:
                continue
            p, note = _threshold_points(v, pts_cfg[sig])
            if p < 0 and neutral_news:
                conflicts.append(f"Headcount drop ({v:+.1f}%, {e.evidence_id}) coincides with "
                                 f"{_v(neutral_news[0], 'subtype')} news ({neutral_news[0].evidence_id}); "
                                 f"not treated as distress.")
                add(sig, 0, [e, neutral_news[0]], note + " (neutralised: corporate event)")
            else:
                add(sig, p, [e], note)

    if primary.role == Role.customer:
        for e in by_sig.get("role_mix", [])[:1]:
            if _v(e, "flag"):
                add("role_mix", pts_cfg.role_mix, [e], "ops+sales share low")

    for e in by_sig.get("hiring", [])[:1]:
        recent, prior = _num(_v(e, "recent")) or 0, _num(_v(e, "prior")) or 0
        if _v(e, "distress_roles"):
            add("hiring", pts_cfg.hiring_distress_roles, [e],
                f"distress roles posted: {', '.join(map(str, _v(e, 'distress_roles')))[:80]}")
        elif prior >= 3 and recent == 0:
            add("hiring", pts_cfg.hiring_stopped, [e], f"hiring stopped ({int(prior)} prior -> 0 recent)")
        else:
            add("hiring", 0, [e], f"{int(recent)} recent vs {int(prior)} prior postings")

    for e in by_sig.get("leadership_exit", [])[:2]:
        add("leadership_exit", pts_cfg.leadership_exit, [e], e.claim[:90])

    head_neg = any(s.points < 0 for s in signals if s.signal.startswith("headcount"))
    for e in by_sig.get("funding_runway", [])[:1]:
        m = _num(_v(e, "months_since_raise"))
        if m is not None and m > pts_cfg.funding_runway_months and head_neg:
            add("funding_runway", pts_cfg.funding_runway, [e],
                f"{m:.0f} months since last raise and headcount falling")
        else:
            add("funding_runway", 0, [e], "runway not a concern" if m is not None else "no funding data")

    # news: worst two relevant articles, floored
    news_scores = []
    for e in by_sig.get("news", []):
        if not _v(e, "relevant", True):
            continue
        cat = _v(e, "category", "minor")
        p = sc.news_severity.get(cat, 0)
        p = 0 if p == "hard_stop" else int(p)
        news_scores.append((p, e, cat))
    news_scores.sort(key=lambda t: (t[0], t[1].evidence_id))
    worst = [t for t in news_scores if t[0] < 0][:2]
    if worst:
        p = max(sum(t[0] for t in worst), int(sc.get("news_floor", -30)))
        add("news", p, [t[1] for t in worst], "; ".join(f"{t[2]}: {t[1].claim[:60]}" for t in worst))
    elif news_scores:
        add("news", 0, [t[1] for t in news_scores[:2]], "no adverse news among relevant articles")
    filtered = [e for e in by_sig.get("news", []) if not _v(e, "relevant", True)]
    if filtered:
        conflicts.append(f"{len(filtered)} namesake/irrelevant article(s) filtered out")

    for e in by_sig.get("employee_reviews", [])[:1]:
        r, n = _num(_v(e, "rating")), _num(_v(e, "count")) or 0
        if r is not None and r < pts_cfg.employee_reviews_min_rating and n >= pts_cfg.employee_reviews_min_count:
            add("employee_reviews", pts_cfg.employee_reviews_low, [e], f"rating {r}/5 from {int(n)} reviews")
        elif r is not None:
            add("employee_reviews", 0, [e], f"rating {r}/5 from {int(n)} reviews")

    if primary.role == Role.customer:
        for e in by_sig.get("web_traffic", [])[:1]:
            pct = _num(_v(e, "pct"))
            if pct is not None and pct <= pts_cfg.web_traffic_drop_pct:
                add("web_traffic", pts_cfg.web_traffic_drop, [e], f"web visitors {pct:+.0f}%")

    for e in by_sig.get("payment_behaviour", [])[:1]:
        dso, terms = _num(_v(e, "dso")), _num(_v(e, "terms"))
        if dso is not None and terms is not None and dso - terms > pts_cfg.payment_dso_over_terms_days:
            add("payment_behaviour", pts_cfg.payment_behaviour, [e], f"DSO {dso:.0f} vs {terms:.0f}-day terms")

    pos_total, pos_evs, pos_kinds = 0, [], []
    for e in by_sig.get("positive", []):
        kind = _v(e, "kind")
        p = int(sc.positive_points.get(kind, 0)) if kind else 0
        if p and kind not in pos_kinds:
            pos_total += p
            pos_evs.append(e)
            pos_kinds.append(kind)
    if pos_evs:
        add("positive", min(pos_total, int(sc.positive_cap)), pos_evs, "positive: " + ", ".join(pos_kinds))

    total += sum(s.points for s in signals)
    total = max(0, min(100, total))

    # 4. rating -----------------------------------------------------------------------
    if total >= sc.green_min:
        rating = Rating.GREEN
    elif total >= sc.amber_min:
        rating = Rating.AMBER
    else:
        rating = Rating.RED

    # 5. coverage gate: never GREEN without enough fresh, independent evidence -------
    excl = [(x["signal"], str(x["value"])) for x in sc.get("coverage_exclude", [])]

    def counts(e: Evidence) -> bool:
        v = e.value if not isinstance(e.value, dict) else e.value.get("status", e.value)
        return (e.signal, str(v)) not in excl

    distinct = {e.signal for e in ok if counts(e)}
    required = set(sc.coverage_required_any)
    if rating == Rating.GREEN and (len(distinct) < int(sc.coverage_min_signals) or not (distinct & required)):
        missing = sorted(required - distinct)
        cannot_verify.append(
            f"CANNOT VERIFY: is {name} financially healthy? Missing: fresh evidence for "
            f"{', '.join(missing) or 'enough independent signals'} (have {len(distinct)}, need "
            f"{sc.coverage_min_signals}). Tried: {_sources_tried(evidence)}.")
        rating = Rating.CANNOT_VERIFY

    # 6. unavailable sources: a failed source can never produce GREEN -----------------
    bad_required = [e for e in unavailable if e.signal in required or e.signal in ("legal_status", "insolvency")]
    if rating == Rating.GREEN and bad_required:
        cannot_verify.append(
            f"HELD: rating would be GREEN but {', '.join(sorted({e.signal for e in bad_required}))} could not be "
            f"checked. Missing: {'; '.join(e.claim for e in bad_required)[:200]}. Retry or review manually.")
        rating = Rating.HELD
    elif rating in (Rating.AMBER, Rating.RED):
        for e in unavailable:
            if e.signal in ("legal_status", "insolvency"):
                cannot_verify.append(f"Note: {e.claim} (a hard-stop check could not run).")

    stale = [e for e in evidence if e.stale]
    if stale:
        conflicts.append(f"{len(stale)} stale signal(s) shown but not scored: "
                         + ", ".join(sorted({e.signal for e in stale})))

    return finish(rating, total)
