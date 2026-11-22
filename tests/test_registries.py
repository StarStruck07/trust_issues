"""Owner: Nilay (P2). Registries: IBBI parsing and matching, blocked portals, legal_status source."""
import pytest

from crd import config
from crd.data import crustdata, registries, signals
from crd.data.crustdata import CrustdataClient
from tests.helpers import cp, resolved


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    monkeypatch.setenv("CRD_AS_OF", "2026-11-22")
    monkeypatch.delenv("CRD_FAIL_SOURCES", raising=False)
    crustdata._BUCKETS.clear()


def check(counterparty, entity=None):
    return registries.check(entity or resolved(), counterparty, CrustdataClient("run-r"), config.get())


def row(kind, date, debtor):
    return (f"<tr><td> Public Announcement of {kind} </td><td> {date} </td><td> x </td><td> {debtor} </td>"
            f"<td> Bank </td><td> IP </td><td><a href=https://ibbi.gov.in/uploads/a.pdf download>pdf</a></td>"
            f"<td></td></tr>")


class FakePublic:
    run_id = "run-r"
    last_raw_ref = "var/runs/run-r/raw/registries_x.json"

    def __init__(self, rows=(), status=200):
        self.html = "<table><tbody>" + "".join(rows) + "</tbody></table>"
        self.status = status

    def public_get(self, url, *, key, day=None):
        return {"url": url, "status": self.status, "html": self.html}


def fake_check(counterparty, client):
    return registries.check(resolved(), counterparty, client, config.get())


def test_recorded_cirp_is_a_hard_stop():
    evs = check(cp(name="Noel Pharma", legal_name="Noel Pharma (India) Private Limited"))
    e = [x for x in evs if x.signal == "insolvency"][0]
    assert e.status == "ok" and e.value["status"] == "cirp_admitted" and e.value["announced"] == "2026-07-25"
    assert e.source_type == "registry" and e.url.endswith(".pdf") and e.field_path == "Type of PA"
    assert e.record_date.isoformat() == "2026-11-22" and not e.stale


def test_recorded_no_announcement_is_none_found():
    evs = check(cp(name="Delhivery", legal_name="Delhivery Limited"))
    e = [x for x in evs if x.signal == "insolvency"][0]
    assert e.status == "ok" and e.value == "none_found"
    assert e.claim.startswith("No IBBI announcement found for Delhivery Limited as of 2026-11-22")


def test_liquidation_maps_to_liquidation():
    c = FakePublic([row("Liquidation Process", "02-09-2026", "ACME LOGISTICS PRIVATE LIMITED")])
    e = fake_check(cp(legal_name="Acme Logistics Pvt Ltd"), c)[0]
    assert e.value["status"] == "liquidation"


def test_old_announcement_is_historical_not_hard_stop():
    c = FakePublic([row("Corporate Insolvency Resolution Process", "15-01-2021", "ACME LOGISTICS PRIVATE LIMITED")])
    e = fake_check(cp(legal_name="Acme Logistics Private Limited"), c)[0]
    assert e.value["status"] == "historical_cirp_admitted" and "not treated as a hard stop" in e.claim


def test_similar_names_are_ambiguous_never_hard_stop():
    c = FakePublic([row("Corporate Insolvency Resolution Process", "01-10-2026", "ACME LOGISTICS SOUTH PRIVATE LIMITED"),
                    row("Liquidation Process", "01-09-2026", "ACME LOGISTICS NORTH LLP")])
    e = fake_check(cp(legal_name="Acme Logistics Private Limited"), c)[0]
    assert e.status == "empty" and e.claim.startswith("ambiguous registry match: 2 companies")


def test_one_similar_name_is_not_a_match():
    c = FakePublic([row("Corporate Insolvency Resolution Process", "01-10-2026", "ACME LOGISTICS SOUTH PRIVATE LIMITED")])
    e = fake_check(cp(legal_name="Acme Logistics Private Limited"), c)[0]
    assert e.value == "none_found" and "similar name not matched" in e.claim


def test_ibbi_down_is_unavailable(monkeypatch):
    monkeypatch.setenv("CRD_FAIL_SOURCES", "registries")
    evs = check(cp(legal_name="Delhivery Limited"))
    assert evs[0].signal == "insolvency" and evs[0].status == "unavailable"


def test_ibbi_http_error_is_unavailable():
    e = fake_check(cp(legal_name="Acme Logistics Private Limited"), FakePublic(status=503))[0]
    assert e.status == "unavailable"


def test_gst_not_checked_is_empty_not_unavailable():
    evs = check(cp(legal_name="Delhivery Limited", gstin="27AAAAA0000A1Z5"))
    gst = [x for x in evs if x.signal == "legal_status"][0]
    assert gst.status == "empty" and "CAPTCHA" in gst.claim


def test_lookup_by_id_returns_none():
    assert registries.lookup_by_id(cin="U12345", gstin="27AAAAA0000A1Z5") is None


def test_normalise_strips_legal_suffixes():
    assert registries.normalise("Noel Pharma (India) Private Limited") == "noel pharma"
    assert registries.normalise("ACME LOGISTICS PVT. LTD.") == "acme logistics"
    assert registries.normalise("Blue Dart Express Ltd") == "blue dart express"


def test_legal_status_comes_from_crustdata_record_and_says_mca_not_checked():
    evs = signals.gather(cp(), resolved(cid=5798, domain="delhivery.com", legal_name="Delhivery"),
                         CrustdataClient("run-r"), config.get())
    ls = [e for e in evs if e.signal == "legal_status"][0]
    assert ls.status == "ok" and ls.value == {"status": "active", "registry_checked": False}
    assert "MCA not checked" in ls.claim and ls.field_path == "basic_info.status"


def test_recorded_namesake_hike_leather_is_not_a_hard_stop():
    """Real IBBI page: 'Hike Leather' is in liquidation; Hike (the app company) is not."""
    evs = check(cp(name="Hike", legal_name="Hike Private Limited"))
    e = [x for x in evs if x.signal == "insolvency"][0]
    assert e.value == "none_found" and "1 similar name not matched" in e.claim


def test_recorded_real_liquidation_go_first():
    evs = check(cp(name="Go First", legal_name="Go Airlines (India) Limited"))
    e = [x for x in evs if x.signal == "insolvency"][0]
    assert e.value["status"] == "liquidation" and e.value["announced"] == "2025-01-25"


def test_recorded_real_cirp_dunzo():
    evs = check(cp(name="Dunzo", legal_name="Dunzo Digital Private Limited"))
    e = [x for x in evs if x.signal == "insolvency"][0]
    assert e.value["status"] == "cirp_admitted" and e.value["announced"] == "2025-09-23"


def test_same_company_in_different_case_counts_once():
    c = FakePublic([row("Corporate Insolvency Resolution Process", "01-10-2026", "ACME LOGISTICS SOUTH PRIVATE LIMITED"),
                    row("Liquidation Process", "01-09-2026", "Acme Logistics South Pvt Ltd")])
    e = fake_check(cp(legal_name="Acme Logistics Private Limited"), c)[0]
    assert e.value == "none_found" and "1 similar name not matched" in e.claim
