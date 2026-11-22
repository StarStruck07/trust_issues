"""Owner: Nilay (P2). STUB from Arth — see NILAY_data.md section 11."""
from __future__ import annotations


def create_watch(domains: list[str], webhook_url: str, every_hours: int = 168) -> str:
    raise NotImplementedError


def verify_signature(headers: dict, raw_body: bytes) -> bool:
    raise NotImplementedError


def parse_payload(body: dict) -> list[str]:
    raise NotImplementedError


def send_test(watch_id: str) -> None:
    raise NotImplementedError
