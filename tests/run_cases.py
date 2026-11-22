"""Run tests/cases/*.yaml end to end and grade them. Owner: Arth (P1). Cases written by Prachi.

  python -m crd.cli test [--case '03_*'] [--mock/--live]
A wrong GREEN is a hard fail. Writes var/test_report.json. Returns exit code 1 if any case fails.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
import tempfile
import time

import yaml

RED, GREEN_T, END = "\033[31m", "\033[32m", "\033[0m"


def _grade(case: dict, run_id: str) -> tuple[bool, str]:
    from crd import db
    exp = case.get("expected", {})
    rows = case.get("input_rows", [])
    target = exp.get("cp_id") or (rows[0]["cp_id"] if rows else None)
    a = db.get_assessment(run_id, target) if target else None
    run = db.get_run(run_id)
    if run and run["status"] == "needs_clarification":
        ok = "NEEDS_CLARIFICATION" in [str(x) for x in (exp.get("rating") if isinstance(exp.get("rating"), list)
                                                         else [exp.get("rating")])]
        return ok, f"clarification asked: {run['note']}"
    if not a:
        return False, f"no assessment for {target} (run status {run and run['status']})"
    want = exp.get("rating")
    want = want if isinstance(want, list) else [want] if want else []
    why = []
    ok = True
    if want and a.rating.value not in want:
        ok = False
        why.append(f"rating {a.rating.value} not in {want}")
        if a.rating.value == "GREEN":
            why.append("WRONG GREEN (hard fail)")
    if a.rating.value in (exp.get("must_not") or []):
        ok = False
        why.append(f"rating {a.rating.value} is forbidden")
    if exp.get("hard_stop") and not a.hard_stop:
        ok = False
        why.append("expected a hard stop")
    if exp.get("merged_cp_ids") and sorted(a.cp_ids) != sorted(exp["merged_cp_ids"]):
        ok = False
        why.append(f"merged {a.cp_ids} != {exp['merged_cp_ids']}")
    ev = db.get_evidence(run_id, a.cp_ids[0])
    hay = " ".join(f"{e.url or ''} {e.source} {e.field_path or ''} {e.passage or ''}" for e in ev).lower()
    for s in exp.get("must_cite") or []:
        if s.lower() not in hay:
            ok = False
            why.append(f"missing citation '{s}'")
    return ok, "; ".join(why) or f"{a.rating.value} score {a.score}"


def run_case(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        case = yaml.safe_load(f)
    # A case may set env vars (e.g. CRD_FAIL_SOURCES for case 10). Restore them, and CRD_AS_OF,
    # afterwards so nothing leaks into the next case.
    env = {k: str(v) for k, v in (case.get("env") or {}).items()}
    saved = {k: os.environ.get(k) for k in [*env, "CRD_AS_OF"]}
    os.environ.update(env)
    try:
        return _run_case(case, path)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run_case(case: dict, path: str) -> dict:
    tmp = tempfile.mkdtemp(prefix="crdcase_")
    os.environ["CRD_DB_PATH"] = os.path.join(tmp, "case.db")
    from crd import db, orchestrator
    db.init_db()
    if case.get("as_of"):
        os.environ["CRD_AS_OF"] = str(case["as_of"])
    csv_path = os.path.join(tmp, "input.csv")
    rows = case.get("input_rows", [])
    keys = sorted({k for r in rows for k in r})
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in r.items()})
    # a case may set env vars for its own run only (case 10: CRD_FAIL_SOURCES=enrich)
    case_env = {k: str(v) for k, v in (case.get("env") or {}).items()}
    saved_env = {k: os.environ.get(k) for k in case_env}
    os.environ.update(case_env)
    t0 = time.time()
    try:
        rid = orchestrator.run_review(case["request"], csv_path)
        ok, why = _grade(case, rid)
        costs = db.run_costs(rid)
    except Exception as e:  # a crash is a fail, not a stop
        ok, why, costs, rid = False, f"crash: {type(e).__name__}: {e}", {"credits": 0}, None
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"id": case.get("id", os.path.basename(path)), "pass": ok, "why": why, "run_id": rid,
            "credits": costs.get("credits", 0), "seconds": round(time.time() - t0, 1)}


def main(pattern: str = "*") -> int:
    paths = sorted(glob.glob(os.path.join("tests", "cases", f"{pattern}.yaml")))
    if not paths:
        print("no cases found in tests/cases/")
        return 1
    db_before = os.environ.get("CRD_DB_PATH")
    results = [run_case(p) for p in paths]
    if db_before:
        os.environ["CRD_DB_PATH"] = db_before
    for r in results:
        c = GREEN_T if r["pass"] else RED
        print(f"{c}{'PASS' if r['pass'] else 'FAIL'}{END}  {r['id']:<28} {r['credits']:>6} cr {r['seconds']:>5}s  {r['why']}")
    n = sum(r["pass"] for r in results)
    print(f"\n{n}/{len(results)} passed")
    os.makedirs("var", exist_ok=True)
    with open("var/test_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "*"))
