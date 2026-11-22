"""Owner: Nilay (P2). Crustdata Watcher: entity watches on our counterparties' domains (NILAY_data.md section 11).

API (docs.crustdata.com, 2025-11-01)
    create   POST /watch/company            {entities, track, config, notifications}  -> {"id", ...}
    test     POST /watch/company/{id}/test  free; sends a test envelope to the webhook
    Baseline run is free and never fires; then 5 credits per notification at the default 30-day refresh.
    `track` and `fields` cannot change after creation, so a different frequency means a new watch.
Webhook
    Header  x-crustdata-signature: t=<unix>,v1=<hex>
            v1 = HMAC-SHA256(key=API key, msg=f"{t}.{raw_body}")
    Header  x-crustdata-event-id: notification id (use for idempotency)
    Envelope (entity watch): {"metadata": {..., "payload_delivery": {"type": "inline"|"link", ...}},
                              "results": [{"changes": [...], "record": {...}}, ...]}
All calls go through CrustdataClient (ledger, rate limit 10/min, mock mode).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from crd import config

LEDGER_RUN_ID = "watcher"
SIGNATURE_HEADER = "x-crustdata-signature"
EVENT_HEADER = "x-crustdata-event-id"
MAX_SKEW_S = int(os.getenv("CRD_WEBHOOK_MAX_SKEW_S", "3600"))   # tolerance not documented; generous for demos


def _client(run_id: str = LEDGER_RUN_ID):
    from crd.data.crustdata import CrustdataClient
    return CrustdataClient(run_id)


def watch_body(domains: list[str], webhook_url: str, every_hours: int = 168) -> dict:
    return {
        "entities": {"domains": sorted({d.strip().lower() for d in domains if d and d.strip()})},
        "track": {"op": "or", "conditions": [
            {"field": "headcount.total", "type": "changed"},
            {"field": "news", "type": "added"},
        ]},
        "config": {"trigger": {"type": "interval", "every_hours": int(every_hours)}, "max_results_per_run": 100},
        "notifications": [{"type": "webhook", "url": webhook_url}],
    }


def create_watch(domains: list[str], webhook_url: str | None = None, every_hours: int = 168) -> str:
    """Create an entity watch on these domains and return its id. Raises ValueError on bad input."""
    url = webhook_url or (config.get().get("webhook_url") or "")
    if not url.startswith("https://"):
        raise ValueError("webhook_url must be a public https URL (ngrok URL + /webhooks/crustdata)")
    body = watch_body(domains, url, every_hours)
    if not body["entities"]["domains"]:
        raise ValueError("create_watch needs at least one domain")
    resp = _client().create_company_watch(body)
    wid = resp.get("id")
    if not wid:
        from crd.data.errors import SourceUnavailable
        raise SourceUnavailable(f"watch create returned no id: {str(resp)[:200]}")
    return str(wid)


def send_test(watch_id: str, notification_endpoint: str | None = None) -> dict:
    """Ask Crustdata to deliver a test notification (free). Returns {"delivered": [...], "envelope": {...}}."""
    body = {"notification_endpoint": notification_endpoint} if notification_endpoint else {}
    return _client().test_company_watch(watch_id, body)


# --------------------------------------------------------------------------- webhook side
def _header(headers, name: str) -> str | None:
    if headers is None:
        return None
    try:
        v = headers.get(name)
        if v is not None:
            return v
    except AttributeError:
        pass
    for k, v in dict(headers).items():
        if str(k).lower() == name:
            return v
    return None


def sign(raw_body: bytes, key: str, ts: int | None = None) -> str:
    """Build a signature header value the way Crustdata does (used by tests and local demos)."""
    t = int(ts if ts is not None else time.time())
    mac = hmac.new(key.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify_signature(headers: dict, raw_body: bytes, *, key: str | None = None, now: float | None = None) -> bool:
    """True only for a valid, recent x-crustdata-signature over the exact raw body."""
    from crd.data.crustdata import _load_key
    key = key or _load_key()
    sig = _header(headers, SIGNATURE_HEADER)
    if not key or not sig or raw_body is None:
        return False
    parts: dict[str, list[str]] = {}
    for item in str(sig).split(","):
        k, _, v = item.strip().partition("=")
        if k and v:
            parts.setdefault(k, []).append(v)
    try:
        t = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - t) > MAX_SKEW_S:
        return False
    expected = hmac.new(key.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for v in parts.get("v1", []))


def event_id(headers: dict, body: dict | None = None) -> str | None:
    """Idempotency key for a delivery: the event-id header, else metadata.notification_id."""
    return _header(headers, EVENT_HEADER) or ((body or {}).get("metadata") or {}).get("notification_id")


def is_test(body: dict) -> bool:
    return bool(((body or {}).get("metadata") or {}).get("test"))


def parse_payload(body: dict) -> list[str]:
    """Company domains that changed. Ignores baseline/no-change entries. Link-mode payloads return []
    (the results sit in a pre-signed file we do not fetch from the webhook handler)."""
    meta = (body or {}).get("metadata") or {}
    if ((meta.get("payload_delivery") or {}).get("type")) == "link":
        return []
    results = (body or {}).get("results") or []
    if isinstance(results, dict):          # discovery-watch shape: {"added": [...]}
        results = results.get("added") or []
    out: list[str] = []
    for r in results:
        if not isinstance(r, dict) or not r.get("changes"):
            continue
        d = _domain(r.get("record") or {})
        if d and d not in out:
            out.append(d)
    return out


def _domain(record: dict) -> str | None:
    bi = record.get("basic_info") or {}
    for v in (bi.get("primary_domain"), record.get("primary_domain"), record.get("domain"),
              (bi.get("all_domains") or [None])[0], bi.get("website"), record.get("website")):
        if v:
            v = str(v).lower().strip()
            v = v.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
            return v or None
    return None
