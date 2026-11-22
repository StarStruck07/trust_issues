"""Stand-in for crd/data/watcher.py (Nilay)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os


def verify_signature(headers, raw: bytes) -> bool:
    secret = os.getenv("CRUSTDATA_WEBHOOK_SECRET")
    if not secret:
        return True  # mock mode: no secret configured
    sig = headers.get("x-crustdata-signature", "")
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


def parse_payload(body) -> list[str]:
    if isinstance(body, (bytes, str)):
        body = json.loads(body or "{}")
    doms = body.get("domains") or [body.get("domain")]
    return [d for d in doms if d]


def create_watch(domains: list[str], webhook_url: str | None, every_hours: int = 24) -> str:
    return "watch_mock_" + hashlib.sha1(",".join(domains).encode()).hexdigest()[:8]
