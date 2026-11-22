"""Seed a FICTIONAL demo portfolio into its own database. Owner: Arth (P1).

    python scripts/seed_demo.py [--db var/demo-portfolio/crd.db] [--vendors 24] [--customers 400]

"Kaveri Freight" is a made-up 3PL. Every company name is invented and every piece of evidence is
synthetic, labelled source="Demo data (fictional)" and raw_ref="demo:synthetic", so the Sources tab and
reports never present it as a Crustdata answer. The verdicts are real outputs: each company goes through
crd.scoring.score() and crd.actions.policy.propose() exactly like a live review.
Never point this at the real database; it refuses to run without a path containing "demo".
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEMO_NOTE = "DEMO: fictional portfolio (Kaveri Freight), synthetic evidence, not real companies"
SRC = "Demo data (fictional)"

A = ["Arya", "Bharat", "Chandra", "Dhruv", "Ekta", "Ganga", "Hari", "Indra", "Jai", "Kiran", "Lakshmi", "Mahi", "Navya",
     "Om", "Prithvi", "Rudra", "Saanvi", "Tara", "Uday", "Vedant", "Yamuna", "Zeal", "Anvi", "Ishan", "Kavya", "Nidhi"]
B = ["Kart", "Basket", "Weaves", "Naturals", "Threads", "Organics", "Home", "Kitchen", "Pets", "Wellness", "Beauty", "Toys",
     "Footwear", "Electricals", "Books", "Grocers", "Decor", "Spices", "Tea Co", "Optics", "Living", "Craft", "Fit", "Labs"]
V_CARRIER = ["Roadlines", "Logistics", "Freight Carriers", "Express Cargo", "Transport Co", "Movers", "Fleet Services"]
V_WH = ["Warehousing", "Logistics Parks", "Storage"]
V_VENDOR = ["Packaging", "Corrugators", "Pallets", "Labels & Tapes", "Forklift Services", "Fuel Cards"]
PLACES = ["Bhiwandi", "Nagpur", "Indore", "Surat", "Hosur", "Sriperumbudur", "Lucknow", "Guwahati", "Ludhiana", "Vapi",
          "Kolhapur", "Raipur", "Vizag", "Coimbatore", "Jaipur", "Patna"]


def ev(run_id, cp_id, signal, value, claim, source_type="crustdata_api", status="ok", days_old=12):
    from crd.models import Evidence
    return Evidence(evidence_id=f"ev_{uuid.uuid4().hex[:8]}", run_id=run_id, cp_id=cp_id, signal=signal, claim=claim,
                    value=value, status=status, source_type=source_type, source=SRC, field_path=f"demo.{signal}",
                    raw_ref="demo:synthetic", record_date=date(2026, 11, 22) - timedelta(days=days_old),
                    retrieved_at=datetime.now(timezone.utc))


def profile(rng: random.Random) -> str:
    """Which story this company tells. Most are healthy; a few are not; some can't be identified."""
    return rng.choices(["healthy", "soft", "drop", "news_bad", "exit", "insolvent", "lookalike", "nofoot", "held", "merger"],
                       weights=[58, 12, 6, 5, 4, 2, 5, 3, 3, 2])[0]


def evidence_for(kind, rng, run_id, cp):
    q = round(rng.uniform(-3, 9), 1)
    h = round(q + rng.uniform(-2, 6), 1)
    base = [ev(run_id, cp.cp_id, "identity", 1.0, f"Matched {cp.name} by domain (confidence 1.00)"),
            ev(run_id, cp.cp_id, "insolvency", "none_found", f"No insolvency announcement found for {cp.name}", "registry")]
    reviews = ev(run_id, cp.cp_id, "employee_reviews", {"rating": round(rng.uniform(3.2, 4.4), 1), "count": rng.randint(25, 900)},
                 "Employee rating from reviews")
    hiring = ev(run_id, cp.cp_id, "hiring", {"recent": rng.randint(1, 30), "prior": rng.randint(1, 30), "distress_roles": []},
                "Job postings, last 30 days vs the 90 before")
    if kind in ("healthy", "merger"):
        out = base + [ev(run_id, cp.cp_id, "headcount_qoq", q, f"Headcount changed {q:+.1f}% over the quarter"),
                      ev(run_id, cp.cp_id, "headcount_6m", h, f"Headcount changed {h:+.1f}% over six months"), hiring, reviews]
        if rng.random() < .25:
            out.append(ev(run_id, cp.cp_id, "positive", {"kind": rng.choice(["recent_raise", "big_contract", "headcount_growth"])},
                          "Positive signal reported"))
        if kind == "merger":
            out[2] = ev(run_id, cp.cp_id, "headcount_qoq", -24.0, "Headcount changed -24.0% over the quarter")
            out.append(ev(run_id, cp.cp_id, "news", {"category": "irrelevant", "subtype": "demerger", "relevant": True},
                          f"{cp.name} completed a demerger of one business line", "web"))
        return out
    if kind == "soft":
        return base + [ev(run_id, cp.cp_id, "headcount_qoq", -11.0, "Headcount changed -11.0% over the quarter"),
                       ev(run_id, cp.cp_id, "headcount_6m", -9.0, "Headcount changed -9.0% over six months"),
                       ev(run_id, cp.cp_id, "employee_reviews", {"rating": 2.7, "count": 140}, "Employee rating 2.7/5 from 140 reviews"),
                       ev(run_id, cp.cp_id, "hiring", {"recent": 0, "prior": 6, "distress_roles": []}, "Hiring stopped")]
    if kind == "drop":
        return base + [ev(run_id, cp.cp_id, "headcount_qoq", -26.0, "Headcount changed -26.0% over the quarter"),
                       ev(run_id, cp.cp_id, "headcount_6m", -31.0, "Headcount changed -31.0% over six months"), hiring]
    if kind == "news_bad":
        cat = rng.choice(["layoffs_or_shutdown", "operations_halt", "lost_client_or_downgrade", "fraud_or_raid"])
        return base + [ev(run_id, cp.cp_id, "headcount_qoq", q, f"Headcount changed {q:+.1f}% over the quarter"), hiring,
                       ev(run_id, cp.cp_id, "news", {"category": cat, "relevant": True},
                          f"{cp.name}: {cat.replace('_', ' ')} reported", "web")]
    if kind == "exit":
        return base + [ev(run_id, cp.cp_id, "headcount_qoq", q, f"Headcount changed {q:+.1f}% over the quarter"), hiring,
                       ev(run_id, cp.cp_id, "news", {"category": "leadership_exit", "relevant": True}, f"{cp.name}: CEO resigned", "web")]
    if kind == "insolvent":
        return [base[0], ev(run_id, cp.cp_id, "insolvency", "cirp_admitted", f"{cp.name} admitted into insolvency (CIRP)", "registry"),
                ev(run_id, cp.cp_id, "headcount_qoq", q, f"Headcount changed {q:+.1f}% over the quarter"), hiring]
    if kind == "held":
        return base + [ev(run_id, cp.cp_id, "headcount_qoq", q, f"Headcount changed {q:+.1f}% over the quarter"), hiring,
                       ev(run_id, cp.cp_id, "news", None, "News source timed out", "web", status="unavailable")]
    return [ev(run_id, cp.cp_id, "identity", None, f"No unique match for '{cp.name}'", status="empty")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="var/demo-portfolio/crd.db")
    ap.add_argument("--vendors", type=int, default=24)
    ap.add_argument("--customers", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if "demo" not in args.db.replace("\\", "/").lower():
        print("refusing: the demo database path must contain 'demo'")
        return 2
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    if Path(args.db).exists():
        Path(args.db).unlink()
    os.environ["CRD_DB_PATH"] = args.db
    os.environ.setdefault("TEAM_INBOX", "team-inbox@example.com")

    from crd import config, db, scoring
    from crd.actions import policy
    from crd.models import Counterparty, ResolvedEntity, ReviewRequest

    rng = random.Random(args.seed)
    cfg = config.get()
    db.init_db()
    req = ReviewRequest(raw_text="Demo portfolio: Kaveri Freight (fictional), weekly review of vendors and customers",
                        as_of=date(2026, 11, 22))
    run_id = db.new_run(req)
    review_id = config.review_id(req.as_of)

    used: set[str] = set()
    cps: list[Counterparty] = []
    for i in range(args.vendors):
        kind = "carrier" if i < 14 else ("warehouse_lessor" if i < 18 else "vendor")
        pool = V_CARRIER if kind == "carrier" else V_WH if kind == "warehouse_lessor" else V_VENDOR
        while True:
            name = f"{rng.choice(PLACES)} {rng.choice(A)} {rng.choice(pool)}"
            if name not in used:
                break
        used.add(name)
        crit = "sole_source" if i in (0, 14) else "primary" if i % 3 == 0 else "backup"
        cps.append(Counterparty(cp_id=f"V-{i + 1:03d}", name=name, role=kind, criticality=crit if kind != "vendor" else "na",
                                importance="strategic" if crit == "sole_source" else "core" if crit == "primary" else "standard",
                                exposure_inr=round(rng.lognormvariate(14.3, .7), -3), account_owner="ops-team",
                                domain=f"{name.lower().replace(' ', '').replace('&', '')[:20]}.example"))
    for i in range(args.customers):
        while True:
            name = f"{rng.choice(A)} {rng.choice(B)}"
            if name not in used:
                break
        used.add(name)
        big = rng.random() < .08
        cps.append(Counterparty(cp_id=f"C-{i + 1:03d}", name=name, role="customer", criticality="na",
                                importance="strategic" if big else rng.choice(["core", "standard", "standard"]),
                                depends_on_us=rng.choice(["high", "medium", "low", "na"]),
                                exposure_inr=round(rng.lognormvariate(13.6 if not big else 15.3, .8), -3),
                                credit_terms_days=rng.choice([15, 30, 30, 45, 60]), account_owner="credit-team",
                                domain=f"{name.lower().replace(' ', '')[:20]}.example"))

    groups, counts = [], {}
    for cp in cps:
        db.upsert_counterparty(cp)
        kind = profile(rng)
        evs = evidence_for(kind, rng, run_id, cp)
        if kind in ("lookalike", "nofoot"):
            ent = ResolvedEntity(cp_ids=[cp.cp_id], status="ambiguous" if kind == "lookalike" else "not_found",
                                 candidates=[{"name": cp.name}, {"name": cp.name + " Pvt Ltd"}] if kind == "lookalike" else [])
        else:
            ent = ResolvedEntity(cp_ids=[cp.cp_id], status="resolved", legal_name=cp.name + " Private Limited",
                                 domain=cp.domain, match_type="domain", confidence=1.0)
        db.save_evidence(evs)
        a = scoring.score([cp], ent, evs, cfg, run_id, None)
        top = next((s for s in sorted(a.signals, key=lambda s: s.points) if s.points < 0), None)
        a.explanation = (a.cannot_verify[0] if a.cannot_verify else
                         f"{a.rating.value}: {top.note if top else 'no material negative signal'} (score {a.score}/100). Demo data.")
        db.save_assessment(a)
        acts = policy.propose(a, cp, cfg, review_id) or []
        a.action_ids = [x.action_id for x in acts]
        db.save_assessment(a)
        for st in ("gather", "score", "propose"):
            db.mark_step(run_id, cp.cp_id, st, "done")
        groups.append({"entity": ent.model_dump(mode="json"), "cp_ids": [cp.cp_id], "evidence_ids": []})
        counts[a.rating.value] = counts.get(a.rating.value, 0) + 1
    db.mark_step(run_id, "__all__", "resolve", "done", {"groups": groups})
    db.finish_run(run_id, "done", note=DEMO_NOTE)
    print(f"seeded {run_id} into {args.db}: {len(cps)} companies ({args.vendors} vendors, {args.customers} customers) {counts}")
    print(f"pending approvals: {len(db.get_actions('pending_approval'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
