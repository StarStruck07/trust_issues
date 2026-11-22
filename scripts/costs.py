"""P2 cost report for one run, from the ledger.

Usage:
    python scripts/costs.py                 # latest run in the DB (CRD_DB_PATH)
    python scripts/costs.py run_e062d856

Prints credits and calls by endpoint, credits per resolved counterparty, the cache-hit rate,
and LLM tokens and rupees. Uses only crd.db functions.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crd import config, db  # noqa: E402


def report(run_id: str) -> dict:
    cfg = config.get()
    led = db.get_ledger(run_id, limit=100000)
    by_ep: dict[str, dict] = defaultdict(lambda: {"calls": 0, "paid_calls": 0, "credits": 0.0})
    for e in led:
        k = f"{e.provider}:{e.endpoint}"
        by_ep[k]["calls"] += 1
        by_ep[k]["credits"] += e.credits
        by_ep[k]["paid_calls"] += 1 if e.status == "ok" and e.credits > 0 else 0
    assessments = db.get_assessments(run_id)
    resolved = [a for a in assessments if a.entity.status == "resolved"]
    credits = sum(e.credits for e in led)
    api = [e for e in led if e.provider in ("crustdata", "registry")]
    cached = sum(1 for e in api if e.status == "cached")
    llm = [e for e in led if e.provider == "anthropic"]
    inr_per_credit = float(cfg.get("inr_per_credit") or 0)
    return {
        "run_id": run_id,
        "counterparties": len(assessments), "resolved": len(resolved),
        "credits_total": round(credits, 2),
        "credits_per_resolved_cp": round(credits / len(resolved), 2) if resolved else None,
        "inr_total": round(credits * inr_per_credit, 2) if inr_per_credit else None,
        "cache_hit_rate": round(cached / len(api), 3) if api else None,
        "by_endpoint": {k: {**v, "credits": round(v["credits"], 2)} for k, v in sorted(by_ep.items())},
        "llm": {"calls": len(llm), "tokens_in": sum(e.tokens_in for e in llm),
                "tokens_out": sum(e.tokens_out for e in llm), "inr": round(sum(e.inr for e in llm), 2),
                "skipped": sum(1 for e in llm if e.note.startswith(("skipped", "mock")))},
    }


def main() -> None:
    db.init_db()
    run_id = sys.argv[1] if len(sys.argv) > 1 else (db.list_runs(1) or [{}])[0].get("run_id")
    if not run_id:
        sys.exit("no runs in the DB")
    r = report(run_id)
    print(f"run {r['run_id']}: {r['counterparties']} counterparties, {r['resolved']} resolved")
    print(f"credits {r['credits_total']}  per resolved cp {r['credits_per_resolved_cp']}  "
          f"INR {r['inr_total'] if r['inr_total'] is not None else '(set inr_per_credit)'}  "
          f"cache-hit rate {r['cache_hit_rate']}")
    for k, v in r["by_endpoint"].items():
        print(f"  {k:<26} calls {v['calls']:>3}  paid {v['paid_calls']:>3}  credits {v['credits']:>7}")
    print(f"LLM: {r['llm']}")


if __name__ == "__main__":
    main()
