"""Owner: Nilay (P2). signals.gather on recorded fixtures (mock mode): emission, sourcing, failure handling."""
from datetime import date

import pytest

from crd import config
from crd.data import crustdata, registries, signals
from crd.data.crustdata import CrustdataClient
from crd.data.errors import BudgetExceeded
from tests.helpers import cp, resolved, ev


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    monkeypatch.setenv("CRD_AS_OF", "2026-11-22")
    monkeypatch.delenv("CRD_FAIL_SOURCES", raising=False)
    crustdata._BUCKETS.clear()


def run(counterparty, entity):
    return signals.gather(counterparty, entity, CrustdataClient("run-s"), config.get())


def by(evs, signal):
    return [e for e in evs if e.signal == signal]


DELHIVERY = dict(cid=5798, domain="delhivery.com", legal_name="Delhivery Limited")


def test_carrier_signals_from_one_enrich_call():
    evs = run(cp(role="carrier"), resolved(**DELHIVERY))
    qoq = by(evs, "headcount_qoq")[0]
    assert qoq.status == "ok" and qoq.value == pytest.approx(2.84, abs=0.01)
    assert qoq.field_path == "headcount.growth_percent.qoq" and qoq.raw_ref.endswith("enrich/delhivery.com.json")
    assert qoq.record_date == date(2026, 11, 16) and not qoq.stale
    assert by(evs, "headcount_6m")[0].value == pytest.approx(4.88, abs=0.01)
    rv = by(evs, "employee_reviews")[0]
    assert rv.value == {"rating": 3.2, "count": 1959}
    fr = by(evs, "funding_runway")[0]
    assert fr.value["months_since_raise"] == 4 and fr.value["last_round"] == "post_ipo_secondary"
    # post-IPO secondary is not cash into the company: no recent_raise positive
    assert not [e for e in by(evs, "positive") if e.value["kind"] == "recent_raise"]
    # supplier: no customer-only signals
    assert not by(evs, "role_mix") and not by(evs, "web_traffic")
    h = by(evs, "hiring")[0]
    assert h.status == "ok" and h.source == "Job Search" and h.value["recent"] > 0
    for e in evs:
        assert e.source and (e.field_path or e.passage or e.status != "ok") and e.retrieved_at
        assert e.cp_id == "C-1" and e.run_id == "run-s"


def test_customer_gets_role_mix_and_web_traffic():
    evs = run(cp(role="customer"), resolved(cid=5844, domain="mamaearth.in", legal_name="Honasa Consumer Limited"))
    rm = by(evs, "role_mix")[0]
    assert set(rm.value) >= {"ops_sales", "finance", "flag"}
    wt = by(evs, "web_traffic")[0]
    assert wt.value["domain"] == "mamaearth.in" and wt.value["pct"] < 0
    # monthly bucket "2026-10-01" covers October: dated 31 Oct, 22 days old on 22 Nov -> fresh
    assert wt.record_date == date(2026, 10, 31) and not wt.stale
    assert not by(evs, "funding_runway")          # no funding data is not a negative


def test_payment_behaviour_from_client_record():
    evs = run(cp(role="customer", dso_days=52, credit_terms_days=45, overdue_inr=120000),
              resolved(**DELHIVERY))
    pb = by(evs, "payment_behaviour")[0]
    assert pb.source_type == "client_record" and pb.value == {"dso": 52, "terms": 45, "overdue_inr": 120000}
    assert "DSO 52 days against 45-day terms" in pb.claim and pb.record_date == date(2026, 11, 22)


def test_unresolved_entity_only_uses_client_record():
    ent = resolved().model_copy(update={"status": "ambiguous", "crustdata_company_id": None})
    evs = run(cp(dso_days=60, credit_terms_days=30), ent)
    assert [e.signal for e in evs] == ["payment_behaviour"]


def test_failed_sources_become_unavailable_never_raise(monkeypatch):
    monkeypatch.setenv("CRD_FAIL_SOURCES", "enrich,job_search")
    evs = run(cp(), resolved(**DELHIVERY))
    hq = by(evs, "headcount_qoq")[0]
    assert hq.status == "unavailable" and "CRD_FAIL_SOURCES" in hq.claim
    assert by(evs, "hiring")[0].status == "unavailable"
    # stubs not yet implemented also degrade to unavailable, never to "no data"
    assert {e.status for e in by(evs, "legal_status")} == {"unavailable"}


def test_registry_hard_stop_skips_news_and_people(monkeypatch):
    def hard(entity, cp_, client, cfg):
        return [ev("legal_status", "struck_off", source_type="registry", source="MCA")]
    calls = []
    monkeypatch.setattr(registries, "check", hard)
    from crd.data import news, people
    monkeypatch.setattr(news, "collect", lambda *a, **k: calls.append("news") or [])
    monkeypatch.setattr(people, "leadership_exits", lambda *a, **k: calls.append("people") or [])
    evs = run(cp(), resolved(**DELHIVERY))
    assert calls == [] and by(evs, "legal_status")[0].value == "struck_off"


def test_budget_errors_propagate(monkeypatch):
    def boom(*a, **k):
        raise BudgetExceeded("cap")
    monkeypatch.setattr(CrustdataClient, "enrich", boom)
    with pytest.raises(BudgetExceeded):
        run(cp(), resolved(**DELHIVERY))


def test_unknown_mock_company_flows():
    evs = run(cp(), resolved(cid=None, domain="example-carrier.in", legal_name="Example Carrier"))
    assert by(evs, "headcount_qoq")[0].status == "ok"
    h = by(evs, "hiring")[0]
    assert h.value["recent"] == 0 and h.value["prior"] == 0


def test_full_job_page_does_not_claim_zero_prior(monkeypatch):
    jobs = [{"metadata": {"date_added": "2026-11-20T10:00:00"}, "job_details": {"title": "Driver"}}] * 25
    monkeypatch.setattr(CrustdataClient, "job_search", lambda self, **kw: jobs)
    h = by(run(cp(), resolved(**DELHIVERY)), "hiring")[0]
    assert h.value["recent"] == 25 and h.value["prior"] is None and "earlier period not measured" in h.claim


def test_monthly_web_traffic_dated_to_month_end():
    from crd.data.signals import _month_end
    assert _month_end(date(2026, 10, 1)) == date(2026, 10, 31) and _month_end(date(2026, 12, 1)) == date(2026, 12, 31)


def test_payment_behaviour_points_to_client_record():
    pb = by(run(cp(dso_days=60, credit_terms_days=30), resolved(**DELHIVERY)), "payment_behaviour")[0]
    assert pb.raw_ref == "counterparties/C-1"
