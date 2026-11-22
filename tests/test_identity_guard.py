"""A lone fuzzy name match must be the same company name, and an unidentified company can still be
RED on a registry notice found by its exact legal name (found while running the demo: 'Quasar India
Limited' had been resolved to 'Quasar Infotek Private Limited - India' at confidence 0.8)."""
import csv
import os

from crd import db, orchestrator
from crd.resolve import _same_name


def test_same_name_rule():
    assert _same_name("Blue Dart", "Blue Dart Express Limited")
    assert _same_name("India Glycols Limited", "India Glycols Limited, Delhi")
    assert not _same_name("Quasar India Limited", "Quasar Infotek Private Limited - India")
    assert not _same_name("Tide", "Tidewater Logistics")


def test_quasar_not_resolved_to_namesake_but_red_on_ibbi(tmp_path, monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    monkeypatch.setenv("CRD_AS_OF", "2026-11-22")
    p = tmp_path / "q.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cp_id", "name", "legal_name", "city", "role", "exposure_inr", "credit_terms_days"])
        w.writerow(["C-103", "Quasar India", "Quasar India Limited", "New Delhi", "customer", "3500000", "45"])
    rid = orchestrator.run_review("Check our customer Quasar India", str(p))
    a = db.get_assessment(rid, "C-103")
    assert a.entity.status == "not_found"
    assert "Infotek" not in a.display_name
    assert a.rating.value == "RED" and a.hard_stop == "cirp_admitted"
    ev = db.get_evidence(rid, "C-103")
    assert any("ibbi" in (e.url or "").lower() for e in ev if e.signal == "insolvency")
