"""Owner: Nilay (P2). CrustdataClient pipeline: retries, 402, cache, budget, mock, forced failures."""
import json

import httpx
import pytest

from crd import db
from crd.data import crustdata
from crd.data.crustdata import CrustdataClient
from crd.data.errors import BudgetExceeded, CreditsExhausted, SourceUnavailable

IDENTIFY_OK = [{"matched_on": "acme.in", "match_type": "domain", "matches": [
    {"confidence_score": 1.0, "company_data": {"crustdata_company_id": 7, "basic_info": {"name": "Acme"}}}]}]


@pytest.fixture(autouse=True)
def live_env(monkeypatch):
    monkeypatch.setenv("CRUSTDATA_API_KEY", "test-key")
    monkeypatch.setenv("CRD_MOCK", "0")
    monkeypatch.delenv("CRD_FAIL_SOURCES", raising=False)
    crustdata._BUCKETS.clear()


def make_client(handler, run_id="run-t", **kw):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request, len(calls))

    c = CrustdataClient(run_id, mock=False, transport=httpx.MockTransport(wrapped), sleep=lambda s: None, **kw)
    return c, calls


def statuses(run_id="run-t"):
    return sorted(e.status for e in db.get_ledger(run_id))


def test_429_then_200_gives_two_ledger_rows_and_success():
    def h(req, n):
        if n == 1:
            return httpx.Response(429, headers={"x-ratelimit-reset": "2"})
        return httpx.Response(200, json=IDENTIFY_OK, headers={"x-credits-used": "0.2"})

    c, calls = make_client(h)
    out = c.identify(domains=["acme.in"])
    assert out[0]["matches"][0]["company_data"]["crustdata_company_id"] == 7
    assert len(calls) == 2
    assert statuses() == ["ok", "rate_limited"]
    assert db.run_costs("run-t")["credits"] == pytest.approx(0.2)


def test_three_timeouts_raise_source_unavailable():
    def h(req, n):
        raise httpx.ReadTimeout("slow", request=req)

    c, calls = make_client(h)
    with pytest.raises(SourceUnavailable):
        c.enrich(domain="acme.in")
    assert len(calls) == 3
    assert statuses() == ["timeout"] * 3


def test_402_raises_credits_exhausted_after_one_call():
    c, calls = make_client(lambda req, n: httpx.Response(402, json={"error": "no credits"}))
    with pytest.raises(CreditsExhausted):
        c.enrich(domain="acme.in")
    assert len(calls) == 1
    assert statuses() == ["error"]


def test_401_is_auth_error_not_retried():
    c, calls = make_client(lambda req, n: httpx.Response(401, json={"message": "Invalid API key in request"}))
    with pytest.raises(SourceUnavailable, match="auth"):
        c.identify(domains=["acme.in"])
    assert len(calls) == 1


def test_500_then_200_retries():
    def h(req, n):
        return httpx.Response(503) if n == 1 else httpx.Response(200, json=IDENTIFY_OK)

    c, calls = make_client(h)
    c.identify(domains=["acme.in"])
    assert len(calls) == 2
    assert statuses() == ["error", "ok"]


def test_cache_hit_makes_no_http_call():
    c, calls = make_client(lambda req, n: httpx.Response(200, json=IDENTIFY_OK, headers={"x-credits-used": "0.2"}))
    c.identify(domains=["acme.in"])
    c2, calls2 = make_client(lambda req, n: httpx.Response(500), run_id="run-2")
    out = c2.identify(domains=["acme.in"])
    assert out == IDENTIFY_OK
    assert len(calls2) == 0
    led = db.get_ledger("run-2")
    assert [e.status for e in led] == ["cached"] and led[0].credits == 0
    assert c2.last_raw_ref and c2.last_raw_ref.endswith(".json")


def test_budget_cap_raises_budget_exceeded(monkeypatch):
    real_get = crustdata.config.get

    def small_budget(*a, **k):
        cfg = real_get(*a, **k)
        cfg["limits"]["max_credits_per_run"] = 3
        return cfg

    monkeypatch.setattr(crustdata.config, "get", small_budget)
    c, calls = make_client(lambda req, n: httpx.Response(
        200, json=[{"matches": [{"company_data": {"crustdata_company_id": n}}]}], headers={"x-credits-used": "2"}))
    c.enrich(domain="a.in")             # 2 credits spent
    with pytest.raises(BudgetExceeded):
        c.enrich(domain="b.in")         # 2 + 2 > 3
    assert len(calls) == 1


def test_per_counterparty_budget(monkeypatch):
    real_get = crustdata.config.get

    def small_cp(*a, **k):
        cfg = real_get(*a, **k)
        cfg["limits"]["max_credits_per_cp"] = 3
        return cfg

    monkeypatch.setattr(crustdata.config, "get", small_cp)
    c, calls = make_client(lambda req, n: httpx.Response(200, json=[], headers={"x-credits-used": "2"}))
    c.enrich(domain="a.in", cp_key="C-1")
    c.enrich(domain="b.in", cp_key="C-2")   # other counterparty is fine
    with pytest.raises(BudgetExceeded):
        c.enrich(domain="c.in", cp_key="C-1")


def test_cost_falls_back_to_config_price_when_no_header():
    c, _ = make_client(lambda req, n: httpx.Response(200, json=IDENTIFY_OK + [{"matches": []}]))
    c.identify(domains=["acme.in", "nobody.in"])
    led = db.get_ledger("run-t")
    assert led[0].credits == pytest.approx(0.2)        # one matched identifier at 0.2, unmatched free


def test_forced_failure_skips_http(monkeypatch):
    monkeypatch.setenv("CRD_FAIL_SOURCES", "enrich,web_search")
    c, calls = make_client(lambda req, n: httpx.Response(200, json=[]))
    with pytest.raises(SourceUnavailable, match="CRD_FAIL_SOURCES"):
        c.enrich(domain="acme.in")
    assert len(calls) == 0
    c.identify(domains=["acme.in"])                    # not listed -> works
    assert len(calls) == 1


def test_mock_reads_recorded_fixture(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    c = CrustdataClient("run-m")
    ident = c.identify(domains=["delhivery.com"])
    cid = ident[0]["matches"][0]["company_data"]["crustdata_company_id"]
    assert cid == 5798
    ed = c.enrich(company_id=cid)                      # found via company-id alias
    assert ed["basic_info"]["primary_domain"] == "delhivery.com"
    assert ed["headcount"]["total"] > 0
    assert c.last_raw_ref.endswith(("enrich/delhivery.com.json", "enrich/5798.json"))
    assert c.job_search(company_id=cid)
    c.web_search('"Delhivery"')                        # query mapped to the recorded fixture
    assert c.last_raw_ref.endswith(("web_search/delhivery.json", "web_search/delhivery.com.json"))
    led = db.get_ledger("run-m")
    assert led and all(e.status == "cached" and e.credits == 0 and e.note == "mock" for e in led)


def test_mock_unknown_name_is_not_found_and_unknown_domain_flows(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    monkeypatch.setenv("CRD_MOCK_SYNTHETIC", "1")  # opt in to the stand-in company
    c = CrustdataClient("run-m")
    assert c.identify(names=["Totally Unknown Pvt Ltd"])[0]["matches"] == []
    ident = c.identify(domains=["example-carrier.in"])
    cid = ident[0]["matches"][0]["company_data"]["crustdata_company_id"]
    ed = c.enrich(company_id=cid)
    assert ed["crustdata_company_id"] == cid and "MOCK" in ed["basic_info"]["description"]
    assert ed["people"]["cxos"] == []
    assert c.job_search(company_id=cid) == []
    assert c.web_fetch(["https://x.example/a"])[0]["success"] is False


def test_mock_lookalike_name_returns_recorded_matches(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    c = CrustdataClient("run-m")
    ms = c.identify(names=["Sharma Transport"])[0]["matches"]
    assert len(ms) >= 2


def test_mock_batch_identify_answers_each_identifier(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    out = CrustdataClient("run-m").identify(domains=["delhivery.com", "bluedart.com", "example-carrier.in"])
    ids = [e["matches"][0]["company_data"]["crustdata_company_id"] for e in out]
    assert ids[:2] == [5798, 736] and len(ids) == 3
    names = CrustdataClient("run-m").identify(names=["Sharma Transport", "Nobody Pvt Ltd"])
    assert len(names) == 2 and names[0]["matches"] and names[1]["matches"] == []


def test_cache_expires_after_ttl(monkeypatch):
    calls = []

    def h(req, n):
        calls.append(n)
        return httpx.Response(200, json=IDENTIFY_OK, headers={"x-credits-used": "0.2"})

    c, _ = make_client(h)
    c.identify(domains=["acme.in"])
    monkeypatch.setenv("CRD_CACHE_TTL_HOURS", "0")      # everything is now too old
    c2, calls2 = make_client(h, run_id="run-3")
    c2.identify(domains=["acme.in"])
    assert len(calls2) == 1                             # fetched again, not served from cache


def test_record_mode_writes_replayable_fixtures(monkeypatch, tmp_path):
    monkeypatch.setenv("CRD_RECORD", "1")
    monkeypatch.setattr(crustdata, "FIXTURES", tmp_path)
    two = [{"matched_on": "a.in", "matches": [{"confidence_score": 1.0, "company_data": {"crustdata_company_id": 1}}]},
           {"matched_on": "b.in", "matches": []}]
    c, _ = make_client(lambda req, n: httpx.Response(200, json=two, headers={"x-credits-used": "0.2"}))
    c.identify(domains=["a.in", "b.in"])
    assert json.loads((tmp_path / "identify" / "a.in.json").read_text(encoding="utf-8"))[0]["matched_on"] == "a.in"
    assert (tmp_path / "identify" / "b.in.json").exists()              # split per identifier
    monkeypatch.setenv("CRD_RECORD", "0")
    monkeypatch.setenv("CRD_MOCK", "1")
    crustdata._INDEX = None
    out = CrustdataClient("run-r").identify(domains=["a.in"])          # replays offline
    assert out[0]["matches"][0]["company_data"]["crustdata_company_id"] == 1
    crustdata._INDEX = None


def test_mock_exact_fixture_beats_alias_from_another_file(monkeypatch):
    """tide.co must resolve to Tide, not to Tide Water Oil whose name search also listed Tide."""
    monkeypatch.setenv("CRD_MOCK", "1")
    crustdata._INDEX = None
    m = CrustdataClient("run-m").identify(domains=["tide.co"])[0]["matches"][0]
    assert m["company_data"]["basic_info"]["name"] == "Tide"


def test_mock_unknown_domain_has_no_fake_company_data(monkeypatch):
    """Default mock mode never invents company data, so it can never rate a company GREEN on it."""
    import pytest

    from crd.data.errors import SourceUnavailable

    monkeypatch.setenv("CRD_MOCK", "1")
    monkeypatch.delenv("CRD_MOCK_SYNTHETIC", raising=False)
    c = CrustdataClient("run-strict")
    with pytest.raises(SourceUnavailable, match="no recorded company data"):
        c.enrich(domain="no-such-company-on-record.in")
