"""Core pipeline on Nilay's real recorded responses (stand-in gather; see tests/standin.py)."""
import csv

from crd import db, orchestrator
from crd.data import signals
from tests import standin


def test_review_on_real_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_client", lambda rid: standin.FixtureClient(rid))
    monkeypatch.setattr(signals, "gather", standin.gather)
    p = tmp_path / "cps.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cp_id", "name", "domain", "role", "criticality", "exposure_inr", "credit_terms_days"])
        w.writerow(["C-001", "Delhivery", "delhivery.com", "carrier", "primary", "5 L", ""])
        w.writerow(["C-002", "Blue Dart", "bluedart.com", "carrier", "backup", "2 L", ""])
        w.writerow(["C-003", "Mamaearth", "mamaearth.in", "customer", "na", "42 L", "45"])
        w.writerow(["C-004", "Sharma Transport", "", "carrier", "backup", "1.5 L", ""])
        w.writerow(["C-005", "Delhivery Limited", "https://www.delhivery.com", "carrier", "", "1 L", ""])
    rid = orchestrator.run_review("Run this week's risk review on all our carriers and customers", str(p))
    by = {a.cp_ids[0]: a for a in db.get_assessments(rid)}
    for a in by.values():
        print(a.cp_ids, a.display_name, a.rating.value, a.score, a.extra.get("top_reason"), "|", a.explanation)
    assert db.get_run(rid)["status"] == "done"
    assert set(by) == {"C-001", "C-002", "C-003", "C-004"}
    assert by["C-001"].cp_ids == ["C-001", "C-005"]                 # duplicate merged
    assert by["C-004"].rating.value == "CANNOT_VERIFY"               # lookalike
    assert all(by[c].rating.value in ("GREEN", "AMBER") for c in ("C-001", "C-002", "C-003"))
    assert all(e.record_date for e in db.get_evidence(rid, "C-001") if e.signal != "identity")
