"""Owner: Nilay (P2). Leadership exits with minimal personal data (NILAY_data.md section 10).

Selection
    Enrich people.cxos holds many self-reported titles (franchise owners calling themselves "CEO", and
    "Office of the CEO" roles). We take at most one person per role (CEO, CFO, COO, MD): a title that
    is exactly the role beats a prefixed one ("COO - City Operations"), then the most-connected profile.
Check
    Person Enrich (basic_profile + experience only). Find the employment entries at this company
    (crustdata_company_id, else normalised employer name). No such entry -> a namesake: skipped.
    Still employed there -> nothing. Left within the last 90 days -> leadership_exit.
    An exit older than 90 days is old news and is not emitted.
Data minimisation
    value keeps only {name, title, left_on, new_employer}. The raw person response is cached for
    reproducibility, and purge_people_raw(run_id) deletes var/runs/<run_id>/raw/person_enrich_* when the
    run ends. We never call Contact Enrichment.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from crd.data.errors import BudgetExceeded, CreditsExhausted
from crd.models import Evidence, ResolvedEntity

ROLES = [  # (label, exact-title regex, prefix regex)
    ("CEO", r"(ceo|chief executive officer)", r"(ceo|chief executive)\b"),
    ("CFO", r"(cfo|chief financial officer)", r"(cfo|chief financial)\b"),
    ("COO", r"(coo|chief operating officer|chief operations officer)", r"(coo|chief operating|chief operations)\b"),
    ("MD", r"(md|managing director)", r"managing director\b"),
]
MAX_PEOPLE = 3
EXIT_WINDOW_DAYS = 90
STOP = (BudgetExceeded, CreditsExhausted)


def leadership_exits(entity: ResolvedEntity, enrich_resp: dict, client, cfg, as_of: date) -> list[Evidence]:
    from crd.data.signals import _Ctx

    ctx = _Ctx(_cp_stub(entity), entity, client, cfg)
    ctx.as_of = as_of
    picks = select_leaders(((enrich_resp or {}).get("people") or {}).get("cxos") or [])
    company_name = entity.legal_name or ((enrich_resp or {}).get("basic_info") or {}).get("name") or "the company"
    out: list[Evidence] = []
    failures: list[str] = []
    for role, person in picks:
        url = profile_url(person)
        if not url:
            continue
        try:
            pdata = ctx.call(client.person_enrich, url)
        except STOP:
            raise
        except Exception as e:  # noqa: BLE001
            failures.append(f"{role}: {type(e).__name__}")
            continue
        raw = getattr(client, "last_raw_ref", None)
        ex = find_exit(pdata, entity, company_name, as_of)
        if not ex:
            continue
        name = ((pdata.get("basic_profile") or {}).get("name")) or ((person.get("basic_profile") or {}).get("name"))
        title = ex["title"] or role
        left = ex["left_on"]
        claim = f"{role} {name} left {company_name} on {left.isoformat()}"
        if ex["new_employer"]:
            claim += f" (now at {ex['new_employer']})"
        out.append(ctx.make_ev(
            "leadership_exit", claim,
            {"name": name, "title": title, "left_on": left.isoformat(), "new_employer": ex["new_employer"]},
            "Person Enrich", "experience[].end_date", as_of, raw_ref=raw))
    if failures:
        out.append(ctx.unavailable("leadership_exit", "Person Enrich", "; ".join(failures)))
    return out


def purge_people_raw(run_id: str) -> None:
    """Delete var/runs/<run_id>/raw/person_enrich_* after a run (data minimisation)."""
    d = Path("var") / "runs" / run_id / "raw"
    if not d.exists():
        return
    for f in d.glob("person_enrich_*"):
        try:
            f.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- selection
def select_leaders(cxos: list[dict]) -> list[tuple[str, dict]]:
    picks: list[tuple[str, dict]] = []
    for label, exact, prefix in ROLES:
        best, best_key = None, None
        for p in cxos:
            title = _norm(((p.get("basic_profile") or {}).get("current_title")) or "")
            if re.fullmatch(exact, title):
                rank = 0
            elif re.match(prefix, title):
                rank = 1
            else:
                continue
            conn = int(((p.get("professional_network") or {}).get("connections")) or 0)
            key = (rank, -conn)
            if best_key is None or key < best_key:
                best, best_key = p, key
        if best is not None and all(best is not q for _, q in picks):
            picks.append((label, best))
        if len(picks) >= MAX_PEOPLE:
            break
    return picks


def profile_url(person: dict) -> str | None:
    sh = (person.get("social_handles") or {}).get("professional_network_identifier") or {}
    url = sh.get("profile_url") or (person.get("professional_network") or {}).get("profile_url")
    return url if isinstance(url, str) and url.startswith("http") else None


# --------------------------------------------------------------------------- exit detection
def find_exit(pdata: dict, entity: ResolvedEntity, company_name: str, as_of: date) -> dict | None:
    """{"title", "left_on", "new_employer"} when the person left this company within the window."""
    from crd.data.signals import _date
    ed = ((pdata or {}).get("experience") or {}).get("employment_details") or {}
    current = [e for e in ed.get("current") or [] if isinstance(e, dict)]
    past = [e for e in ed.get("past") or [] if isinstance(e, dict)]

    def here(e: dict) -> bool:
        cid = e.get("crustdata_company_id")
        if cid is not None and entity.crustdata_company_id is not None:
            return str(cid) == str(entity.crustdata_company_id)
        return bool(_core(e.get("name"))) and _core(e.get("name")) == _core(company_name)

    cur_here = [e for e in current if here(e) and not _date(e.get("end_date"))]
    if cur_here:
        return None                                   # still there
    ended_here = [(e, _date(e.get("end_date"))) for e in past + current if here(e) and _date(e.get("end_date"))]
    if not ended_here:
        return None                                   # never tied to this company: namesake, skip
    e, left = max(ended_here, key=lambda t: t[1])
    if not (as_of - timedelta(days=EXIT_WINDOW_DAYS) <= left <= as_of):
        return None                                   # old news
    new = next((c.get("name") for c in current if not here(c) and not _date(c.get("end_date"))), None)
    return {"title": e.get("title"), "left_on": left, "new_employer": new}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _core(name: str | None) -> str:
    from crd.data.registries import normalise
    return normalise(name)


def _cp_stub(entity: ResolvedEntity):
    """people.leadership_exits gets no Counterparty; _Ctx only needs cp_id and role for evidence."""
    from crd.models import Counterparty, Role
    return Counterparty(cp_id=entity.cp_ids[0] if entity.cp_ids else "unknown", name=entity.legal_name or "unknown",
                        role=Role.vendor)
