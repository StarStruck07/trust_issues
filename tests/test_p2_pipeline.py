"""Owner: Nilay (P2). Pipeline-level checks of the data layer, in mock mode on recorded fixtures.

1. Resilience: any source (or all of them) failing never crashes a run and never yields a GREEN it shouldn't.
2. Real scenarios: companies recorded live on 2026-11-22 (IBBI insolvency, liquidation, namesakes) score as
   expected through signals.gather + Arth's scoring.
3. Webhook: Prachi's /webhooks/crustdata route accepts only correctly signed Crustdata deliveries.
"""
import json

import pytest

from crd import config, db, orchestrator, scoring
from crd.data import crustdata, signals, watcher
from crd.data.crustdata import CrustdataClient
from crd.models import Counterparty, ResolvedEntity

SAMPLE = "data/input/sample_counterparties.csv"
ALL_SOURCES = ["identify", "enrich", "job_search", "web_search", "web_fetch", "person_enrich", "registries", "llm"]
# sources whose loss must hold back a GREEN (identity, hard-stop checks, required coverage signals)
BLOCKING = {"identify", "enrich", "job_search", "web_search", "registries"}


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    monkeypatch.setenv("CRD_AS_OF", "2026-11-22")
    monkeypatch.delenv("CRD_FAIL_SOURCES", raising=False)
    # the sample CSV's example-*.in companies have no recording: these wiring tests use the stand-in
    monkeypatch.setenv("CRD_MOCK_SYNTHETIC", "1")
    crustdata._BUCKETS.clear()


def review(monkeypatch, fail: str = "") -> tuple[str, dict]:
    if fail:
        monkeypatch.setenv("CRD_FAIL_SOURCES", fail)
    rid = orchestrator.run_review("Run this week's risk review on all our carriers and customers", SAMPLE)
    return rid, {a.cp_ids[0]: a for a in db.get_assessments(rid)}


# --------------------------------------------------------------------------- 1. resilience
def test_baseline_mock_review():
    rid = orchestrator.run_review("Run this week's risk review on all our carriers and customers", SAMPLE)
    by = {a.cp_ids[0]: a.rating.value for a in db.get_assessments(rid)}
    assert db.get_run(rid)["status"] == "done"
    assert by == {"C-001": "GREEN", "C-002": "GREEN", "C-003": "CANNOT_VERIFY"}


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_one_source_down_never_crashes(monkeypatch, source):
    rid, by = review(monkeypatch, source)
    assert db.get_run(rid)["status"] == "done", db.get_run(rid)
    assert set(by) == {"C-001", "C-002", "C-003"}
    if source in BLOCKING:
        assert "GREEN" not in {a.rating.value for a in by.values()}, {k: a.rating.value for k, a in by.items()}
    # the failure is visible in the ledger, never silent (the mock sample companies have no articles or
    # CXOs, so web_fetch and person_enrich are never called here: see the Delhivery test below)
    if source not in ("web_fetch", "person_enrich"):
        led = db.get_ledger(rid, limit=10000)
        assert any(e.note.startswith("forced by CRD_FAIL_SOURCES") for e in led)


@pytest.mark.parametrize("source,signal", [("web_fetch", "news"), ("person_enrich", "leadership_exit")])
def test_fetch_and_people_failures_on_real_delhivery_data(monkeypatch, source, signal):
    monkeypatch.setenv("CRD_FAIL_SOURCES", source)
    a, ev = score_scenario("C-001", "Delhivery", "Delhivery Limited", "delhivery.com", "carrier", None, None)
    bad = [e for e in ev if e.signal == signal]
    assert bad and all(e.status == "unavailable" for e in bad)
    if signal == "news":
        assert a.rating.value != "GREEN"        # news is a required signal: unchecked news can't be GREEN


def test_every_source_down_holds_everything(monkeypatch):
    rid, by = review(monkeypatch, ",".join(ALL_SOURCES))
    assert db.get_run(rid)["status"] == "done"
    assert {a.rating.value for a in by.values()} <= {"HELD", "CANNOT_VERIFY"}


# --------------------------------------------------------------------------- 2. recorded real scenarios
SCENARIOS = [
    # cp_id, name, legal name, domain, role, dso, terms, expected ratings, expected hard stop
    ("S-001", "Dunzo", "Dunzo Digital Private Limited", "dunzo.com", "customer", 95, 30, {"RED"}, "cirp_admitted"),
    ("S-002", "Go First", "Go Airlines (India) Limited", "flygofirst.com", "carrier", None, None, {"RED"}, "liquidation"),
    ("S-003", "Byju's", "Think and Learn Private Limited", "byjus.com", "customer", 50, 45, {"RED"}, "insolvency_news"),
    ("S-004", "Hike", "Hike Private Limited", "hike.in", "customer", 28, 30, {"GREEN", "AMBER"}, None),
    ("S-005", "Rivigo", "Rivigo Services Private Limited", "rivigo.com", "carrier", None, None, {"GREEN"}, None),
    ("C-001", "Delhivery", "Delhivery Limited", "delhivery.com", "carrier", None, None, {"GREEN"}, None),
    ("C-003", "Mamaearth", "Honasa Consumer Limited", "mamaearth.in", "customer", 62, 45, {"AMBER"}, None),
]


def score_scenario(cp_id, name, legal, domain, role, dso, terms):
    cfg = config.get()
    client = CrustdataClient("run-scn")
    cp = Counterparty(cp_id=cp_id, name=name, legal_name=legal, domain=domain, role=role,
                      dso_days=dso, credit_terms_days=terms, exposure_inr=1_000_000)
    m = client.identify(domains=[domain])[0]["matches"][0]
    cd = m["company_data"]
    ent = ResolvedEntity(cp_ids=[cp_id], status="resolved", crustdata_company_id=cd["crustdata_company_id"],
                         legal_name=cd["basic_info"]["name"], domain=domain, match_type="domain",
                         confidence=m["confidence_score"])
    ev = signals.gather(cp, ent, client, cfg)
    return scoring.score([cp], ent, ev, cfg, "run-scn"), ev


@pytest.mark.parametrize("row", SCENARIOS, ids=[s[1] for s in SCENARIOS])
def test_recorded_real_company(row):
    *args, expected, hard_stop = row
    a, ev = score_scenario(*args)
    assert a.rating.value in expected, (a.rating.value, a.score, a.hard_stop, [s.note for s in a.signals])
    assert a.hard_stop == hard_stop
    for e in ev:   # rule 4: every ok fact is sourced and dated
        if e.status == "ok":
            assert e.source and (e.field_path or e.passage) and (e.url or e.raw_ref) and e.record_date, e


def test_namesake_registry_hit_does_not_hard_stop_hike():
    a, ev = score_scenario("S-004", "Hike", "Hike Private Limited", "hike.in", "customer", 28, 30)
    ins = [e for e in ev if e.signal == "insolvency"][0]
    assert ins.value == "none_found" and "HIKE LEATHER" in ins.claim.upper()


# --------------------------------------------------------------------------- 3. webhook route
@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv("CRUSTDATA_API_KEY", "test-key")
    from fastapi.testclient import TestClient
    from crd.web import app as webapp
    queued = []
    monkeypatch.setattr(webapp, "_bg", lambda fn, *args: queued.append(args))
    return TestClient(webapp.app), queued


PAYLOAD = {"metadata": {"watch_id": "w1", "notification_id": "n-1", "payload_delivery": {"type": "inline"}},
           "results": [{"changes": [{"field": "headcount.total", "type": "changed"}],
                        "record": {"basic_info": {"primary_domain": "delhivery.com"}}}]}


def test_webhook_rejects_unsigned_and_forged(web):
    client, queued = web
    raw = json.dumps(PAYLOAD).encode()
    assert client.post("/webhooks/crustdata", content=raw).status_code == 401
    forged = {"x-crustdata-signature": watcher.sign(raw, "attacker-key")}
    assert client.post("/webhooks/crustdata", content=raw, headers=forged).status_code == 401
    assert queued == []


def test_webhook_accepts_signed_delivery_once(web):
    client, queued = web
    raw = json.dumps(PAYLOAD).encode()
    h = {"x-crustdata-signature": watcher.sign(raw, "test-key"), "x-crustdata-event-id": "evt-1"}
    r1 = client.post("/webhooks/crustdata", content=raw, headers=h)
    assert r1.status_code == 200 and r1.json()["queued"] == ["delhivery.com"]
    r2 = client.post("/webhooks/crustdata", content=raw, headers=h)
    assert r2.json().get("duplicate") is True and len(queued) == 1
