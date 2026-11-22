"""Owner: Nilay (P2). Watcher: create body, signature check, payload parsing, mock mode."""
import json

import httpx
import pytest

from crd.data import crustdata, watcher
from crd.data.crustdata import CrustdataClient

KEY = "test-key"
BODY = json.dumps({"metadata": {"notification_id": "n1", "payload_delivery": {"type": "inline"}},
                   "results": []}).encode()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("CRUSTDATA_API_KEY", KEY)
    monkeypatch.delenv("CRD_FAIL_SOURCES", raising=False)
    crustdata._BUCKETS.clear()


def test_valid_signature_passes():
    h = {"x-crustdata-signature": watcher.sign(BODY, KEY, ts=1_800_000_000)}
    assert watcher.verify_signature(h, BODY, now=1_800_000_010) is True


def test_header_lookup_is_case_insensitive():
    h = {"X-Crustdata-Signature": watcher.sign(BODY, KEY, ts=1_800_000_000)}
    assert watcher.verify_signature(h, BODY, now=1_800_000_000) is True


@pytest.mark.parametrize("mutate", [
    lambda h, b: (h, b + b" "),                                              # body changed
    lambda h, b: ({"x-crustdata-signature": watcher.sign(b, "other-key", ts=1_800_000_000)}, b),  # wrong key
    lambda h, b: ({}, b),                                                    # no header
    lambda h, b: ({"x-crustdata-signature": "t=abc,v1=00"}, b),              # junk
])
def test_bad_signatures_fail(mutate):
    h, b = mutate({"x-crustdata-signature": watcher.sign(BODY, KEY, ts=1_800_000_000)}, BODY)
    assert watcher.verify_signature(h, b, now=1_800_000_000) is False


def test_old_timestamp_is_rejected():
    h = {"x-crustdata-signature": watcher.sign(BODY, KEY, ts=1_800_000_000)}
    assert watcher.verify_signature(h, BODY, now=1_800_000_000 + watcher.MAX_SKEW_S + 1) is False


def test_parse_payload_returns_changed_domains_only():
    body = {"metadata": {"payload_delivery": {"type": "inline"}}, "results": [
        {"changes": [{"field": "headcount.total"}], "record": {"basic_info": {"primary_domain": "Delhivery.com"}}},
        {"changes": [], "record": {"basic_info": {"primary_domain": "baseline.in"}}},
        {"changes": [{"field": "news"}], "record": {"basic_info": {"website": "https://www.bluedart.com/"}}},
        {"changes": [{"field": "news"}], "record": {"basic_info": {"primary_domain": "delhivery.com"}}},
    ]}
    assert watcher.parse_payload(body) == ["delhivery.com", "bluedart.com"]


def test_link_mode_and_empty_payloads():
    assert watcher.parse_payload({"metadata": {"payload_delivery": {"type": "link", "url": "https://s3"}}}) == []
    assert watcher.parse_payload({}) == []


def test_event_id_and_test_flag():
    body = {"metadata": {"notification_id": "n-9", "test": True}}
    assert watcher.event_id({"x-crustdata-event-id": "e-1"}, body) == "e-1"
    assert watcher.event_id({}, body) == "n-9" and watcher.is_test(body)


def test_create_watch_body_and_call(monkeypatch):
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(201, json={"id": "w_123", "status": "active"})

    monkeypatch.setenv("CRD_MOCK", "0")
    monkeypatch.setattr(watcher, "_client", lambda run_id="watcher": CrustdataClient(
        run_id, mock=False, transport=httpx.MockTransport(handler), sleep=lambda s: None))
    wid = watcher.create_watch(["Delhivery.com", "bluedart.com", ""], "https://abc.ngrok.app/webhooks/crustdata",
                               every_hours=24)
    assert wid == "w_123" and seen["path"] == "/watch/company" and seen["auth"] == f"Bearer {KEY}"
    b = seen["body"]
    assert b["entities"] == {"domains": ["bluedart.com", "delhivery.com"]}
    assert b["config"]["trigger"] == {"type": "interval", "every_hours": 24}
    assert {"field": "news", "type": "added"} in b["track"]["conditions"]
    assert b["notifications"] == [{"type": "webhook", "url": "https://abc.ngrok.app/webhooks/crustdata"}]


def test_create_watch_rejects_non_https():
    with pytest.raises(ValueError):
        watcher.create_watch(["a.in"], "http://localhost:8000/webhooks/crustdata")


def test_mock_mode_create_and_test(monkeypatch):
    monkeypatch.setenv("CRD_MOCK", "1")
    wid = watcher.create_watch(["delhivery.com"], "https://abc.ngrok.app/webhooks/crustdata")
    assert wid == "mock-watch-0001"
    out = watcher.send_test(wid)
    assert watcher.parse_payload(out["envelope"]) == ["delhivery.com"]
