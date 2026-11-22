"""Fictional demo data for mock mode. All company names are invented."""
from __future__ import annotations

import hashlib
import uuid

from crd.fakes import db
from crd.fakes.models import (
    Assessment, Counterparty, Evidence, LedgerEntry, Rating, ResolvedEntity, SignalScore,
)

COUNTERPARTIES = [
    Counterparty(cp_id="C-101", name="Northline Freight", legal_name="Northline Freight Limited",
                 domain="northlinefreight.example", role="carrier", criticality="primary",
                 exposure_inr=0, credit_terms_days=0, alt_suppliers=["Brightway Carriers", "Coastal Roadlines"],
                 importance="core"),
    Counterparty(cp_id="C-102", name="Kestrel Home", legal_name="Kestrel Home Private Limited",
                 domain="kestrelhome.example", role="customer", exposure_inr=4_200_000,
                 credit_terms_days=45, importance="core", depends_on_us="low"),
    Counterparty(cp_id="C-103", name="Orbit Retail", legal_name="Orbit Retail Private Limited",
                 domain="orbitretail.example", role="customer", exposure_inr=3_500_000,
                 credit_terms_days=45, importance="core", depends_on_us="medium"),
    Counterparty(cp_id="C-104", name="Shree Transport", role="carrier", criticality="backup"),
    Counterparty(cp_id="C-201", name="Lumen Foods", role="customer", exposure_inr=1_800_000,
                 credit_terms_days=30, importance="strategic", depends_on_us="high"),
    Counterparty(cp_id="C-202", name="Lumen Foods India Pvt Ltd", legal_name="Lumen Foods India Private Limited",
                 domain="lumenfoods.example", role="customer", exposure_inr=900_000, credit_terms_days=30,
                 importance="strategic", depends_on_us="high", account_owner="Ritika (key accounts)"),
    Counterparty(cp_id="C-105", name="Vardhan Warehousing", legal_name="Vardhan Warehousing LLP",
                 domain="vardhanwh.example", role="warehouse_lessor", criticality="sole_source"),
    Counterparty(cp_id="C-106", name="Pinecrest Logistics", legal_name="Pinecrest Logistics Private Limited",
                 domain="pinecrestlog.example", role="carrier", criticality="sole_source",
                 alt_suppliers=["Northline Freight"]),
    Counterparty(cp_id="C-107", name="Brisk Basket", legal_name="Brisk Basket Private Limited",
                 domain="briskbasket.example", role="customer", exposure_inr=650_000,
                 credit_terms_days=30, importance="standard", depends_on_us="low"),
]


def cp_by_id(cp_id: str) -> Counterparty | None:
    return next((c for c in COUNTERPARTIES if c.cp_id == cp_id), None)


def _ev(run_id, cp_id, signal, claim, **kw) -> Evidence:
    eid = "ev_" + hashlib.sha1(f"{run_id}{cp_id}{signal}{claim}".encode()).hexdigest()[:6]
    return Evidence(evidence_id=eid, run_id=run_id, cp_id=cp_id, signal=signal, claim=claim,
                    retrieved_at="2026-11-22T10:30:00+00:00", **kw)


def build(run_id: str):
    """Returns (assessments, evidence) for one review run."""
    E, A = [], []

    # C-101 healthy carrier -> GREEN
    e1 = _ev(run_id, "C-101", "headcount_qoq", "Headcount changed +3.1% over the last quarter",
             value=3.1, source="Crustdata Company Enrich", field_path="headcount.growth_percent.qoq",
             raw_ref="C-101_company_enrich.json", record_date="2026-11-15")
    e2 = _ev(run_id, "C-101", "hiring", "42 open roles, steady vs last month", value=42,
             source="Crustdata Company Enrich", field_path="job_openings.count",
             raw_ref="C-101_company_enrich.json", record_date="2026-11-15")
    e3 = _ev(run_id, "C-101", "news", "No adverse news since 1 Sep 2026", status="empty",
             source="Web Search", record_date="2026-11-22")
    E += [e1, e2, e3]
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-101"], display_name="Northline Freight", role="carrier",
        entity=ResolvedEntity(match_type="exact", confidence=0.98, legal_name="Northline Freight Limited",
                              domain="northlinefreight.example", cin="L60231MH1998PLC000001"),
        rating=Rating.GREEN, score=82,
        signals=[SignalScore(signal="headcount_qoq", points=+5, evidence_ids=[e1.evidence_id], note="qoq +3.1% -> +5"),
                 SignalScore(signal="hiring", points=+7, evidence_ids=[e2.evidence_id], note="hiring steady -> +7"),
                 SignalScore(signal="news", points=0, evidence_ids=[e3.evidence_id], note="no adverse news")],
        explanation=f"Headcount grew 3.1% last quarter [{e1.evidence_id}] and hiring is steady [{e2.evidence_id}]. No adverse news since September.",
        extra={"city": "Mumbai"},
    ))

    # C-102 distressed customer -> RED (layoffs + headcount drop)
    e4 = _ev(run_id, "C-102", "headcount_qoq", "Headcount changed -22.1% over the last quarter",
             value=-22.1, source="Crustdata Company Enrich", field_path="headcount.growth_percent.qoq",
             raw_ref="C-102_company_enrich.json", record_date="2026-11-14")
    e5 = _ev(run_id, "C-102", "news", "Kestrel Home lays off 30% of staff", source_type="news",
             source="example-news.in", url="https://example-news.in/kestrel-home-layoffs",
             passage="The company let go of nearly a third of its workforce this week.",
             record_date="2026-11-03")
    e6 = _ev(run_id, "C-102", "leadership", "CFO exit reported", source="Crustdata Person Enrich",
             field_path="past_employers[0].end_date", raw_ref="C-102_person_enrich.json",
             record_date="2026-10-20", stale=False)
    E += [e4, e5, e6]
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-102"], display_name="Kestrel Home", role="customer",
        entity=ResolvedEntity(match_type="exact", confidence=0.95, legal_name="Kestrel Home Private Limited",
                              domain="kestrelhome.example"),
        rating=Rating.RED, score=38, change_since_last="AMBER → RED",
        signals=[SignalScore(signal="headcount_qoq", points=-20, evidence_ids=[e4.evidence_id], note="qoq -22.1% <= -20% -> -20"),
                 SignalScore(signal="news", points=-20, evidence_ids=[e5.evidence_id], note="layoffs_or_shutdown"),
                 SignalScore(signal="leadership", points=-8, evidence_ids=[e6.evidence_id], note="CFO exit in 90 days")],
        exposure_inr=4_200_000, inr_at_risk=2_520_000,
        explanation=f"Headcount fell 22.1% last quarter [{e4.evidence_id}] and the company announced layoffs of about 30% [{e5.evidence_id}].",
    ))

    # C-103 insolvency -> RED hard stop
    e7 = _ev(run_id, "C-103", "insolvency", "CIRP admitted by NCLT; public announcement published",
             source_type="registry", source="IBBI", url="https://ibbi.gov.in/en/public-announcement",
             passage="Public announcement of corporate insolvency resolution process.",
             record_date="2026-10-28")
    e8 = _ev(run_id, "C-103", "mca_status", "MCA status unavailable: portal requires CAPTCHA",
             status="unavailable", source="MCA21", record_date=None)
    E += [e7, e8]
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-103"], display_name="Orbit Retail", role="customer",
        entity=ResolvedEntity(match_type="exact", confidence=0.97, legal_name="Orbit Retail Private Limited",
                              domain="orbitretail.example", cin="U52100DL2015PTC000003"),
        rating=Rating.RED, score=0, hard_stop="cirp_admitted",
        signals=[SignalScore(signal="insolvency", points=-70, evidence_ids=[e7.evidence_id], note="hard stop: CIRP admitted")],
        exposure_inr=3_500_000, inr_at_risk=3_500_000,
        explanation=f"An IBBI public announcement shows CIRP admitted on 28 Oct 2026 [{e7.evidence_id}]. This is a hard stop.",
        conflicts=["MCA status could not be read to confirm the IBBI record"],
    ))

    # C-104 lookalike -> CANNOT_VERIFY
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-104"], display_name="Shree Transport", role="carrier",
        entity=ResolvedEntity(match_type="ambiguous", confidence=0.31, candidates=[
            {"name": "Shree Transport Co", "city": "Surat", "domain": None, "cin": None, "score": 0.31},
            {"name": "Shree Transport Services Pvt Ltd", "city": "Indore", "domain": "shreets.example", "cin": "U60210MP2009PTC000004", "score": 0.29},
            {"name": "Shree Transports", "city": "Ludhiana", "domain": None, "cin": None, "score": 0.24},
        ]),
        rating=Rating.CANNOT_VERIFY, score=70,
        cannot_verify=["CANNOT VERIFY: which 'Shree Transport' is our carrier. Missing: domain, CIN or city. Tried: Crustdata Identify (3 candidates), MCA name search."],
        explanation="We found three companies with this name and none matches on a domain, CIN or city. A person needs to confirm which one we work with.",
    ))

    # C-201 + C-202 duplicate merged -> AMBER
    e9 = _ev(run_id, "C-202", "headcount_6m", "Headcount changed -11.4% over 6 months", value=-11.4,
             source="Crustdata Company Enrich", field_path="headcount.growth_percent.six_months",
             raw_ref="C-202_company_enrich.json", record_date="2026-11-12")
    e10 = _ev(run_id, "C-202", "hiring", "Open roles down from 18 to 4", value=4,
              source="Crustdata Company Enrich", field_path="job_openings.count",
              raw_ref="C-202_company_enrich.json", record_date="2026-11-12")
    E += [e9, e10]
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-202", "C-201"], display_name="Lumen Foods India Pvt Ltd", role="customer",
        entity=ResolvedEntity(match_type="exact", confidence=0.93, legal_name="Lumen Foods India Private Limited",
                              domain="lumenfoods.example"),
        rating=Rating.AMBER, score=55, change_since_last="GREEN → AMBER",
        signals=[SignalScore(signal="headcount_6m", points=-10, evidence_ids=[e9.evidence_id], note="6m -11.4% <= -10% -> -10"),
                 SignalScore(signal="hiring", points=-5, evidence_ids=[e10.evidence_id], note="openings -78% -> -5")],
        exposure_inr=2_700_000, inr_at_risk=810_000,
        explanation=f"Headcount fell 11.4% over six months [{e9.evidence_id}] and open roles dropped from 18 to 4 [{e10.evidence_id}]. Rows C-201 and C-202 are the same company and were merged.",
    ))

    # C-105 HELD (registry timeout)
    e11 = _ev(run_id, "C-105", "insolvency", "IBBI search timed out after 3 retries", status="unavailable",
              source="IBBI", record_date=None)
    E.append(e11)
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-105"], display_name="Vardhan Warehousing", role="warehouse_lessor",
        entity=ResolvedEntity(match_type="exact", confidence=0.9, legal_name="Vardhan Warehousing LLP",
                              domain="vardhanwh.example"),
        rating=Rating.HELD, score=70,
        cannot_verify=["HELD: is Vardhan Warehousing in insolvency? Missing: IBBI result. Tried: IBBI search x3 (timeout)."],
        explanation="The insolvency registry timed out, so we did not treat silence as good news. Held for a re-run.",
    ))

    # C-106 sole-source carrier AMBER -> backup RFQ
    e12 = _ev(run_id, "C-106", "headcount_qoq", "Headcount changed -12.0% over the last quarter", value=-12.0,
              source="Crustdata Company Enrich", field_path="headcount.growth_percent.qoq",
              raw_ref="C-106_company_enrich.json", record_date="2026-11-16")
    e13 = _ev(run_id, "C-106", "news", "Pinecrest Logistics loses anchor client", source_type="news",
              source="example-logistics-daily.in", url="https://example-logistics-daily.in/pinecrest-anchor-client",
              passage="The carrier's largest e-commerce contract was not renewed.", record_date="2026-10-11",
              stale=True)
    E += [e12, e13]
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-106"], display_name="Pinecrest Logistics", role="carrier",
        entity=ResolvedEntity(match_type="exact", confidence=0.96, legal_name="Pinecrest Logistics Private Limited",
                              domain="pinecrestlog.example"),
        rating=Rating.AMBER, score=52,
        signals=[SignalScore(signal="headcount_qoq", points=-10, evidence_ids=[e12.evidence_id], note="qoq -12.0% <= -10% -> -10"),
                 SignalScore(signal="news", points=-8, evidence_ids=[e13.evidence_id], note="lost anchor client")],
        explanation=f"Headcount fell 12% last quarter [{e12.evidence_id}] and its largest contract was not renewed [{e13.evidence_id}]. It is our sole source on its lanes.",
    ))

    # C-107 small customer, can switch easily -> AMBER, terms email despite exposure < 10 L
    e14 = _ev(run_id, "C-107", "headcount_6m", "Headcount changed -14.8% over 6 months", value=-14.8,
              source="Crustdata Company Enrich", field_path="headcount.growth_percent.six_months",
              raw_ref="C-107_company_enrich.json", record_date="2026-11-13")
    e15 = _ev(run_id, "C-107", "news", "Brisk Basket exits two cities", source_type="news",
              source="example-startup-news.in", url="https://example-startup-news.in/brisk-basket-exits",
              passage="The quick-commerce startup is shutting dark stores in two cities.", record_date="2026-11-05")
    E += [e14, e15]
    A.append(Assessment(
        run_id=run_id, cp_ids=["C-107"], display_name="Brisk Basket", role="customer",
        entity=ResolvedEntity(match_type="exact", confidence=0.94, legal_name="Brisk Basket Private Limited",
                              domain="briskbasket.example"),
        rating=Rating.AMBER, score=50,
        signals=[SignalScore(signal="headcount_6m", points=-12, evidence_ids=[e14.evidence_id], note="6m -14.8% <= -10% -> -12"),
                 SignalScore(signal="news", points=-8, evidence_ids=[e15.evidence_id], note="market exit")],
        exposure_inr=650_000, inr_at_risk=260_000,
        explanation=f"Headcount fell 14.8% over six months [{e14.evidence_id}] and it is exiting two cities [{e15.evidence_id}].",
    ))

    # copy each row's relationship profile into extra, as the orchestrator does
    for a in A:
        cp = cp_by_id(a.cp_ids[0])
        if cp:
            a.extra.update({"criticality": cp.criticality, "importance": cp.importance,
                            "depends_on_us": cp.depends_on_us})
    return A, E


LEDGER_PLAN = [
    ("crustdata", "/screener/identify", 1.0, 0),
    ("crustdata", "/screener/company", 3.0, 0),
    ("crustdata", "/screener/person/enrich", 2.0, 0),
    ("registry", "ibbi.gov.in/public-announcement", 0, 0),
    ("web", "web_search", 0, 0),
    ("anthropic", "messages (extract)", 0, 1800),
]
INR_PER_CREDIT = 1.6
INR_PER_1K_TOKENS = 0.25


def ledger_rows(run_id: str, cp_id: str, cached: bool = False) -> list[LedgerEntry]:
    out = []
    for prov, ep, cr, tok in LEDGER_PLAN:
        st = "cached" if cached and prov == "crustdata" else "ok"
        if cp_id == "C-105" and prov == "registry":
            st = "error"
        c = 0 if st == "cached" else cr
        out.append(LedgerEntry(
            call_id="call_" + uuid.uuid4().hex[:8], run_id=run_id, provider=prov, endpoint=ep,
            request_hash=hashlib.sha1(f"{cp_id}{ep}".encode()).hexdigest()[:10], credits=c,
            inr=round(c * INR_PER_CREDIT + tok / 1000 * INR_PER_1K_TOKENS, 2),
            tokens_in=int(tok * 0.8), tokens_out=int(tok * 0.2),
            latency_ms=3000 if st == "error" else 180 + (hash(ep) % 600),
            status=st, note="timeout after 3 retries" if st == "error" else cp_id,
            ts=db.now(),
        ))
    return out


def seed_demo() -> str:
    """A finished run so every page has something to show on first start."""
    from crd.actions import policy
    from crd.deps import config

    run_id = "run_demo"
    db.create_run(run_id, "Run this week's risk review on all our carriers and customers",
                  "2026-11-22", status="done", n=len(COUNTERPARTIES))
    A, E = build(run_id)
    for e in E:
        db.save_evidence(e)
    rv = config.review_id("2026-11-22")
    for a in A:
        for cid in a.cp_ids[:1]:
            for le in ledger_rows(run_id, cid):
                db.add_ledger(le)
        acts = policy.propose(a, cp_by_id(a.cp_ids[0]), config.get(), rv)
        a.action_ids = [x.action_id for x in acts]
        db.save_assessment(a)
    import json

    steps = {"resolve": "done", "gather": "done", "score": "done", "propose": "done"}
    prog = {a.cp_ids[0]: {"display_name": a.display_name, **steps,
                          **({"gather": "error"} if a.cp_ids[0] == "C-105" else {})} for a in A}
    db.update_run(run_id, finished_at=db.now(), doc=json.dumps({"progress": prog}))
    return run_id
