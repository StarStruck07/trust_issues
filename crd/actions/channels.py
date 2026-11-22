"""Owner: Prachi (P3). STUB from Arth — see PRACHI_product.md section 7.1."""
from __future__ import annotations


def send_email(key: str, to: str, subject: str, body: str) -> str:
    raise NotImplementedError


def erp_hold(key: str, cp_id: str, reason: str) -> str:
    raise NotImplementedError


def slack_alert(key: str, text: str) -> str:
    raise NotImplementedError


def raise_watch(key: str, domain: str) -> str:
    raise NotImplementedError
