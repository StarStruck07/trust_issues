"""Owner: Nilay (P2). Turn one counterparty's external data into cited Evidence (NILAY_data.md section 7).

gather() order, cheapest and most decisive first:
    registries.check -> enrich (one call, all field groups) -> derived Enrich signals -> job_search
    -> news.collect -> people.leadership_exits
A registry hard stop skips news and people (scoring returns RED anyway).

gather() never raises for a single source: a failure becomes one Evidence(status="unavailable") for the
signal it would have fed. Only BudgetExceeded and CreditsExhausted propagate (the orchestrator stops).

Record dates: Enrich does not return updated_at/indexed_at in practice (tests/fixtures/README.md), so each
signal uses the freshest date its own data carries (headcount timeseries, web traffic timeseries, review
metadata), then updated_at, indexed_at, else None with "refresh date unknown" in the claim.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from crd import config
from crd.data.errors import BudgetExceeded, CreditsExhausted
from crd.models import Counterparty, Evidence, ResolvedEntity, Role

ENRICH_FIELDS = ["basic_info", "headcount", "funding", "people", "news", "employee_reviews",
                 "web_traffic", "hiring"]
OPS_SALES_ROLES = ("Operations", "Sales", "Business Development", "Customer Success and Support")
FINANCE_ROLES = ("Finance", "Accounting")
ROLE_MIX_MIN_PCT = 25.0
DISTRESS_WORDS = ("collection", "recovery", "restructuring", "insolvency", "receivable")
NOT_A_RAISE = ("secondary",)          # post-IPO secondary: shareholders sell, company gets no cash
POSITIVE_QOQ_PCT = 10.0
RECENT_RAISE_MONTHS = 6
JOB_PAGE = 25

STOP = (BudgetExceeded, CreditsExhausted)


# --------------------------------------------------------------------------- evidence helpers
class _Ctx:
    def __init__(self, cp: Counterparty, entity: ResolvedEntity, client, cfg):
        self.cp = cp
        self.entity = entity
        self.client = client
        self.cfg = cfg
        self.run_id = getattr(client, "run_id", None) or "run_unknown"
        self.cp_id = entity.cp_ids[0] if entity.cp_ids else cp.cp_id
        self.as_of: date = config.as_of(cfg)
        self.staleness = int(cfg.get("staleness_days") or 45)

    def call(self, fn: Callable, *args, **kwargs):
        """Pass cp_key for the per-counterparty budget when the client supports it."""
        try:
            if "cp_key" in inspect.signature(fn).parameters:
                kwargs["cp_key"] = self.cp_id
        except (TypeError, ValueError):
            pass
        return fn(*args, **kwargs)

    def make_ev(self, signal: str, claim: str, value: Any, source: str, field_path: str | None,
                record_date: date | None, *, source_type: str = "crustdata_api", url: str | None = None,
                passage: str | None = None, status: str = "ok", raw_ref: str | None = None) -> Evidence:
        if record_date is None and status == "ok":
            claim = f"{claim} (refresh date unknown)"
        stale = bool(record_date and (self.as_of - record_date).days > self.staleness)
        return Evidence(
            evidence_id="ev_" + uuid.uuid4().hex[:8], run_id=self.run_id, cp_id=self.cp_id, signal=signal,
            claim=claim, value=value, status=status, source_type=source_type, source=source,
            field_path=field_path, passage=passage, url=url, raw_ref=raw_ref, record_date=record_date,
            retrieved_at=datetime.now(timezone.utc), stale=stale)

    def unavailable(self, signal: str, source: str, reason: Any, source_type: str = "crustdata_api") -> Evidence:
        msg = f"{type(reason).__name__}: {reason}" if isinstance(reason, Exception) else str(reason)
        if isinstance(reason, NotImplementedError):
            msg = "not implemented yet"
        return self.make_ev(signal, f"{source} unavailable: {msg}"[:300], None, source, None, None,
                            source_type=source_type, status="unavailable")


def _date(v: Any) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _last_ts_date(series: Any) -> date | None:
    dates = [_date(p.get("date")) for p in series or [] if isinstance(p, dict)]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


# --------------------------------------------------------------------------- entry point
def gather(cp: Counterparty, entity: ResolvedEntity, client, cfg) -> list[Evidence]:
    """Never raises for a single source; failed sources become Evidence(status='unavailable')."""
    ctx = _Ctx(cp, entity, client, cfg)
    out: list[Evidence] = []
    out += payment_behaviour(ctx)
    if entity.status != "resolved":
        return out

    # 1. registries (hard stops)
    hard_stop = False
    try:
        from crd.data import registries
        reg = list(registries.check(entity, cp, client, cfg) or [])
        out += reg
        hs = {str(x).lower() for x in (cfg.get("scoring") or {}).get("hard_stops", [])}
        hard_stop = any(e.status == "ok" and e.signal in ("legal_status", "insolvency")
                        and str(e.value.get("status") if isinstance(e.value, dict) else e.value).lower() in hs
                        for e in reg)
    except STOP:
        raise
    except Exception as e:  # noqa: BLE001
        out.append(ctx.unavailable("legal_status", "Registry check", e, "registry"))
        out.append(ctx.unavailable("insolvency", "Registry check", e, "registry"))

    # 2. enrich + derived signals (one paid call)
    enrich: dict = {}
    try:
        enrich = ctx.call(client.enrich, company_id=entity.crustdata_company_id,
                          domain=None if entity.crustdata_company_id else entity.domain,
                          fields=ENRICH_FIELDS) or {}
        raw = getattr(client, "last_raw_ref", None)
        if not enrich:
            out.append(ctx.make_ev("headcount_qoq", "Company Enrich returned no record for this company", None,
                                   "Company Enrich", None, None, status="empty", raw_ref=raw))
        else:
            out += enrich_signals(ctx, enrich, raw)
    except STOP:
        raise
    except Exception as e:  # noqa: BLE001
        out.append(ctx.unavailable("headcount_qoq", "Company Enrich", e))

    # 3. hiring via Job Search
    try:
        out += hiring(ctx)
    except STOP:
        raise
    except Exception as e:  # noqa: BLE001
        out.append(ctx.unavailable("hiring", "Job Search", e))

    if hard_stop:
        return out

    # 4. news
    try:
        from crd.data import news
        out += list(news.collect(entity, cp, enrich, client, cfg) or [])
    except STOP:
        raise
    except Exception as e:  # noqa: BLE001
        out.append(ctx.unavailable("news", "News (Web Search + Web Fetch)", e, "web"))

    # 5. leadership exits
    try:
        from crd.data import people
        out += list(people.leadership_exits(entity, enrich, client, cfg, ctx.as_of) or [])
    except STOP:
        raise
    except Exception as e:  # noqa: BLE001
        out.append(ctx.unavailable("leadership_exit", "Person Enrich", e))

    return out


# --------------------------------------------------------------------------- client record
def payment_behaviour(ctx: _Ctx) -> list[Evidence]:
    cp = ctx.cp
    if cp.dso_days is None or cp.credit_terms_days is None:
        return []
    claim = f"DSO {cp.dso_days:.0f} days against {cp.credit_terms_days}-day terms"
    if cp.overdue_inr:
        claim += f"; ₹{cp.overdue_inr:,.0f} overdue"
    return [ctx.make_ev("payment_behaviour", claim,
                        {"dso": cp.dso_days, "terms": cp.credit_terms_days, "overdue_inr": cp.overdue_inr},
                        "client receivables record", "dso_days", ctx.as_of, source_type="client_record")]


# --------------------------------------------------------------------------- enrich-derived
def enrich_signals(ctx: _Ctx, d: dict, raw: str | None) -> list[Evidence]:
    out: list[Evidence] = []
    fallback_date = _date(d.get("updated_at")) or _date(d.get("indexed_at"))
    is_customer = ctx.cp.role == Role.customer

    h = d.get("headcount") or {}
    h_date = _last_ts_date(h.get("timeseries")) or fallback_date
    growth = h.get("growth_percent") or {}
    total = h.get("total")
    qoq = _num(growth.get("qoq"))
    six = _num(growth.get("six_months"))
    if qoq is not None:
        out.append(ctx.make_ev("headcount_qoq", f"Headcount changed {qoq:+.1f}% over the last quarter (total {total})",
                               round(qoq, 2), "Company Enrich", "headcount.growth_percent.qoq", h_date, raw_ref=raw))
        if qoq > POSITIVE_QOQ_PCT:
            out.append(ctx.make_ev("positive", f"Headcount grew {qoq:+.1f}% over the last quarter",
                                   {"kind": "headcount_growth"}, "Company Enrich",
                                   "headcount.growth_percent.qoq", h_date, raw_ref=raw))
    if six is not None:
        out.append(ctx.make_ev("headcount_6m", f"Headcount changed {six:+.1f}% over the last six months (total {total})",
                               round(six, 2), "Company Enrich", "headcount.growth_percent.six_months", h_date,
                               raw_ref=raw))

    roles = h.get("by_role_percent") or {}
    if is_customer and roles:
        ops = round(sum(_num(roles.get(r)) or 0 for r in OPS_SALES_ROLES), 1)
        fin = round(sum(_num(roles.get(r)) or 0 for r in FINANCE_ROLES), 1)
        flag = ops < ROLE_MIX_MIN_PCT
        out.append(ctx.make_ev(
            "role_mix", f"Ops+sales share {ops}% of staff (flagged below {ROLE_MIX_MIN_PCT:.0f}%)" if flag
            else f"Ops+sales share {ops}% of staff",
            {"ops_sales": ops, "finance": fin, "flag": flag, "threshold_pct": ROLE_MIX_MIN_PCT},
            "Company Enrich", "headcount.by_role_percent", h_date, raw_ref=raw))

    f = d.get("funding") or {}
    raised = _date(f.get("last_fundraise_date"))
    if raised:
        months = max(0, _months_between(raised, ctx.as_of))
        rtype = f.get("last_round_type") or "unknown round"
        amount = _num(f.get("last_round_amount_usd"))
        out.append(ctx.make_ev(
            "funding_runway", f"Last raised {rtype} on {raised.isoformat()} ({months} months ago)",
            {"months_since_raise": months, "last_round": rtype, "amount_usd": amount},
            "Company Enrich", "funding.last_fundraise_date", fallback_date or h_date, raw_ref=raw))
        if months < RECENT_RAISE_MONTHS and not any(w in str(rtype).lower() for w in NOT_A_RAISE):
            amt = f" of ${amount / 1e6:,.1f}M" if amount else ""
            out.append(ctx.make_ev("positive", f"Raised a {rtype} round{amt} {months} months ago",
                                   {"kind": "recent_raise"}, "Company Enrich", "funding.last_fundraise_date",
                                   fallback_date or h_date, raw_ref=raw))

    r = d.get("employee_reviews") or {}
    overall = r.get("overall_rating")
    rating = _num(overall.get("rating")) if isinstance(overall, dict) else _num(overall)
    count = (overall.get("total_count") if isinstance(overall, dict) else None) or r.get("review_count")
    if rating is not None:
        meta = r.get("metadata") or {}
        r_date = _date(meta.get("rating_as_of_date")) or _date(meta.get("last_scraped_at")) or fallback_date or h_date
        out.append(ctx.make_ev("employee_reviews", f"Employee rating {rating}/5 from {count or 0} reviews",
                               {"rating": rating, "count": int(count or 0)}, "Company Enrich",
                               "employee_reviews.overall_rating.rating", r_date, raw_ref=raw))

    if is_customer:
        ev = _web_traffic(ctx, d, raw)
        if ev:
            out.append(ev)
    return out


def _web_traffic(ctx: _Ctx, d: dict, raw: str | None) -> Evidence | None:
    traffic = ((d.get("web_traffic") or {}).get("domain_traffic")) or {}
    if not traffic:
        return None
    domain = ctx.entity.domain or ((d.get("basic_info") or {}).get("primary_domain"))
    entry = traffic.get(domain) if domain else None
    if entry is None:  # fall back to the company's busiest domain
        domain, entry = max(traffic.items(), key=lambda kv: _num((kv[1] or {}).get("monthly_visitors")) or 0)
    series = [p for p in (entry or {}).get("monthly_visitors_timeseries") or []
              if isinstance(p, dict) and _num(p.get("monthly_visitors")) is not None]
    series.sort(key=lambda p: str(p.get("date")))
    if len(series) >= 2:
        start = series[-4] if len(series) >= 4 else series[0]   # ~3 months back
        a, b = _num(start["monthly_visitors"]), _num(series[-1]["monthly_visitors"])
        if not a:
            return None
        pct = (b - a) / a * 100
        claim = (f"Monthly web visitors to {domain} changed {pct:+.0f}% "
                 f"({_date(start['date'])} to {_date(series[-1]['date'])})")
        return ctx.make_ev("web_traffic", claim, {"pct": round(pct, 1), "from": a, "to": b, "domain": domain},
                           "Company Enrich", "web_traffic.domain_traffic.monthly_visitors_timeseries",
                           _date(series[-1]["date"]), raw_ref=raw)
    pct = _num((entry or {}).get("qoq_pct"))
    if pct is None:
        return None
    return ctx.make_ev("web_traffic", f"Monthly web visitors to {domain} changed {pct:+.0f}% quarter on quarter",
                       {"pct": pct, "from": None, "to": _num(entry.get("monthly_visitors")), "domain": domain},
                       "Company Enrich", "web_traffic.domain_traffic.qoq_pct",
                       _date(d.get("updated_at")) or _date(d.get("indexed_at")), raw_ref=raw)


# --------------------------------------------------------------------------- hiring
def hiring(ctx: _Ctx) -> list[Evidence]:
    """Postings in the last 30 days vs the 90 days before that, plus distress-role titles."""
    e = ctx.entity
    jobs = ctx.call(ctx.client.job_search, company_id=e.crustdata_company_id,
                    domain=None if e.crustdata_company_id else e.domain, days=120, limit=JOB_PAGE)
    raw = getattr(ctx.client, "last_raw_ref", None)
    recent_cut = ctx.as_of - timedelta(days=30)
    prior_cut = ctx.as_of - timedelta(days=120)
    recent = prior = 0
    distress: list[str] = []
    latest: date | None = None
    for j in jobs or []:
        meta = j.get("metadata") or {}
        d = _date(meta.get("date_added")) or _date(meta.get("date_updated"))
        if d is None or d > ctx.as_of:
            continue
        latest = max(latest, d) if latest else d
        if d > recent_cut:
            recent += 1
        elif d > prior_cut:
            prior += 1
        else:
            continue
        title = str((j.get("job_details") or {}).get("title") or "")
        if any(w in title.lower() for w in DISTRESS_WORDS) and title not in distress:
            distress.append(title)
    page_full = len(jobs or []) >= JOB_PAGE
    more = "+" if page_full and recent >= JOB_PAGE else ""
    claim = f"{recent}{more} job postings in the last 30 days vs {prior} in the 90 before"
    if page_full and recent < JOB_PAGE:
        claim += f" (latest {JOB_PAGE} postings checked)"
    if distress:
        claim += f"; distress roles: {', '.join(distress[:3])}"
    return [ctx.make_ev("hiring", claim,
                        {"recent": recent, "prior": prior, "distress_roles": distress[:5], "page_full": page_full},
                        "Job Search", "job_listings[].metadata.date_added", ctx.as_of, raw_ref=raw)]
