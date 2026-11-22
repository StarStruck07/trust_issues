"""Command line. Owner: Arth (P1). This is the screening interface — keep commands stable.

  python -m crd.cli init
  python -m crd.cli review "Run this week's risk review on all carriers and customers" --input data/input/counterparties.csv
  python -m crd.cli resume
  python -m crd.cli status [RUN_ID]
  python -m crd.cli why CP_ID [--run RUN_ID]
  python -m crd.cli approve ACTION_ID --by NAME | reject ACTION_ID --by NAME | execute | actions
  python -m crd.cli costs [RUN_ID]
  python -m crd.cli test [--case GLOB]
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional

import typer
from dotenv import load_dotenv

load_dotenv()

from crd import db  # noqa: E402

app = typer.Typer(add_completion=False, help="Counterparty Risk Desk")


def _mode(mock: Optional[bool]) -> None:
    if mock is not None:
        os.environ["CRD_MOCK"] = "1" if mock else "0"


def _summary(run_id: str) -> None:
    run = db.get_run(run_id) or {}
    typer.echo(f"\nrun {run_id}  status={run.get('status')}  {run.get('note') or ''}")
    for a in db.get_assessments(run_id):
        reason = a.extra.get("top_reason") or (a.cannot_verify[0] if a.cannot_verify else "")
        typer.echo(f"  {a.cp_ids[0]:<10} {a.display_name[:28]:<28} {a.rating.value:<14} {a.score:>3}  "
                   f"₹{a.inr_at_risk:>12,.0f}  {reason[:70]}")
    c = db.run_costs(run_id)
    typer.echo(f"  cost: {c['credits']} credits, ₹{c['inr']}, {c['calls']} calls ({c['cached']} cached, "
               f"{c['errors']} errors)")
    typer.echo(f"  outputs: var/runs/{run_id}/")


@app.command()
def init():
    db.init_db()
    typer.echo(f"db ready at {db.db_path()}")


@app.command()
def review(text: str, input: Optional[str] = typer.Option(None, "--input", "-i"),
           mock: Optional[bool] = typer.Option(None, "--mock/--live")):
    _mode(mock)
    from crd import orchestrator
    rid = orchestrator.run_review(text, input)
    run = db.get_run(rid)
    if run and run["status"] == "needs_clarification":
        typer.echo(f"Question before I run: {run['note']}")
        raise typer.Exit(2)
    _summary(rid)


@app.command()
def resume(mock: Optional[bool] = typer.Option(None, "--mock/--live")):
    _mode(mock)
    from crd import orchestrator
    rid = orchestrator.resume()
    if rid:
        _summary(rid)
    else:
        typer.echo("nothing to resume (pending approved actions were executed)")


@app.command()
def status(run_id: Optional[str] = typer.Argument(None)):
    db.init_db()
    if run_id:
        _summary(run_id)
        return
    for r in db.list_runs():
        c = db.run_costs(r["run_id"])
        typer.echo(f"{r['run_id']}  {r['status']:<20} {r['started_at'][:19]}  {c['credits']} cr  "
                   f"{(r['request'] or {}).get('raw_text', '')[:60]}")


@app.command()
def why(cp_id: str, run: Optional[str] = typer.Option(None, "--run")):
    from crd import explain
    rid = run or (db.list_runs(1)[0]["run_id"] if db.list_runs(1) else None)
    if not rid:
        raise typer.Exit("no runs")
    w = explain.why(rid, cp_id)
    if not w:
        raise typer.Exit(f"no assessment for {cp_id} in {rid}")
    typer.echo(f"{cp_id}: {w['rating']}  score {w['final_score']}  (start {w['start']})")
    if w["hard_stop"]:
        typer.echo(f"  HARD STOP: {w['hard_stop']}")
    for s in w["signals"]:
        typer.echo(f"  {s['points']:>+5}  {s['signal']:<18} {s['note'][:80]}")
        for e in s["evidence"]:
            typer.echo(f"          [{e['id']}] {e['claim'][:70]} | {e['source']} {e['field_path'] or ''} "
                       f"{e['url'] or ''} ({e['record_date']})")
    cov = w["coverage"]
    typer.echo(f"  coverage: {'passed' if cov['passed'] else 'FAILED'}  ok={cov['ok_signals']}")
    for line in w["cannot_verify"] + w["unavailable"] + w["conflicts"]:
        typer.echo(f"  ! {line}")
    typer.echo(f"  explanation: {w['explanation']}")


@app.command()
def actions(status: Optional[str] = typer.Option(None)):
    for a in db.get_actions(status):
        typer.echo(f"{a.action_id}  {a.status.value:<17} {a.type.value:<22} {a.cp_id:<10} {a.idempotency_key}")


@app.command()
def approve(action_id: str, by: str = typer.Option(..., "--by")):
    a = db.get_action(action_id)
    if not a:
        raise typer.Exit("unknown action")
    ok = db.transition_action(action_id, "pending_approval", "approved", approved_by=by,
                              approved_at=datetime.now(timezone.utc),
                              draft_hash=hashlib.sha256(a.draft.encode()).hexdigest())
    typer.echo("approved" if ok else f"not approved: action is {a.status.value}")
    if ok:
        from crd.actions import executor
        typer.echo(f"executed: {executor.execute_approved()}")


@app.command()
def reject(action_id: str, by: str = typer.Option(..., "--by")):
    ok = db.transition_action(action_id, "pending_approval", "rejected", approved_by=by)
    typer.echo("rejected" if ok else "not pending")


@app.command()
def execute():
    from crd.actions import executor
    executor.reconcile()
    typer.echo(f"executed: {executor.execute_approved()}")


@app.command()
def costs(run_id: Optional[str] = typer.Argument(None)):
    rid = run_id or db.list_runs(1)[0]["run_id"]
    typer.echo(json.dumps(db.run_costs(rid), indent=2))


@app.command()
def test(case: str = typer.Option("*", "--case"), mock: Optional[bool] = typer.Option(None, "--mock/--live")):
    _mode(mock)
    from tests.run_cases import main
    raise typer.Exit(main(case))


if __name__ == "__main__":
    app()
