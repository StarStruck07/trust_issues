"""End-to-end core flow with a fake client + fake gather; simulated kill and resume."""
import csv

import pytest

from crd import db, orchestrator
from crd.data import signals
from tests.helpers import FakeClient, ev

TABLE = {"acme.in": [("Acme Logistics", "acme.in", 1.0, 7, "Pune")],
         "beta.in": [("Beta Retail", "beta.in", 1.0, 8, "Mumbai")],
         "Gamma Traders": []}
GATHERS = []


def fake_gather(cp, entity, client, cfg):
    GATHERS.append(cp.cp_id)
    if cp.cp_id == "C-2":
        return [ev("headcount_qoq", -25.0), ev("hiring", {"recent": 0, "prior": 6}),
                ev("news", {"category": "layoffs_or_shutdown", "relevant": True}, url="https://news.example/x")]
    return [ev("headcount_qoq", 3.0), ev("hiring", {"recent": 4, "prior": 6}),
            ev("employee_reviews", {"rating": 4.2, "count": 80})]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    GATHERS.clear()
    monkeypatch.setattr(orchestrator, "_client", lambda rid: FakeClient(TABLE))
    monkeypatch.setattr(signals, "gather", fake_gather)
    p = tmp_path / "cps.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Website", "Type", "Outstanding", "Terms"])
        w.writerow(["Acme Logistics", "https://www.acme.in/", "Transporter", "5 L", "30 days"])
        w.writerow(["Beta Retail", "beta.in", "Customer", "₹42,00,000", "45"])
        w.writerow(["Gamma Traders", "", "client", "1.2 Cr", "60"])
        w.writerow(["Acme Logistics Pvt Ltd", "acme.in", "carrier", "1 L", ""])
    monkeypatch.setenv("CRD_MOCK", "1")
    return str(p)


def test_full_run(setup):
    rid = orchestrator.run_review("Run this week's risk review on all carriers and customers", setup)
    run = db.get_run(rid)
    assert run["status"] == "done", run
    by = {a.cp_ids[0]: a for a in db.get_assessments(rid)}
    assert set(by) == {"ROW-001", "ROW-002", "ROW-003"}          # ROW-004 merged into ROW-001
    assert by["ROW-001"].cp_ids == ["ROW-001", "ROW-004"] and by["ROW-001"].rating.value == "GREEN"
    assert by["ROW-003"].rating.value == "CANNOT_VERIFY"
    assert by["ROW-001"].exposure_inr == 600000


def test_kill_and_resume(setup, monkeypatch):
    calls = {"n": 0}

    def crashing(cp, entity, client, cfg):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("simulated kill -9")
        return fake_gather(cp, entity, client, cfg)

    monkeypatch.setattr(signals, "gather", crashing)
    with pytest.raises(KeyboardInterrupt):
        orchestrator.run_review("Run this week's review on all counterparties", setup)
    rid = db.last_unfinished_run()
    assert rid and db.get_run(rid)["status"] == "running"
    monkeypatch.setattr(signals, "gather", fake_gather)
    GATHERS.clear()
    assert orchestrator.resume() == rid
    assert db.get_run(rid)["status"] == "done"
    assert "ROW-001" not in GATHERS                                # finished entity not re-fetched
    assert len(db.get_assessments(rid)) == 3


def test_clarification(setup):
    orchestrator.run_review("noop", setup)
    rid = orchestrator.run_review("Check 'Zeta Freight' before we sign them", None)
    assert db.get_run(rid)["status"] == "needs_clarification"
