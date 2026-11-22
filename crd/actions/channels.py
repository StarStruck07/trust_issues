"""Side-effect channels (PRACHI_product.md section 7.1).

Every channel writes its record keyed by the action's idempotency key *first*.
Calling a channel twice with the same key returns the first record's id and
does nothing else, so a retry or a reconcile can never double-send.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from email.message import EmailMessage

from crd.deps import db, watcher

log = logging.getLogger("crd.channels")


def send_email(key: str, to: str, subject: str, body: str) -> str:
    inbox = os.getenv("TEAM_INBOX")
    if inbox:
        to = inbox  # during the event every message goes to our own inbox only
    ref = db.outbox_insert(key, "email", to, subject, body)
    if ref is None:
        return db.outbox_get(key)["outbox_id"]  # already sent
    _maybe_smtp(to, subject, body)
    return ref


def _maybe_smtp(to: str, subject: str, body: str) -> None:
    host, inbox = os.getenv("SMTP_HOST"), os.getenv("TEAM_INBOX")
    if not host or not inbox or to != inbox:
        return  # the outbox row is the record
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM", inbox)
    msg["To"] = inbox
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as s:
            s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            s.send_message(msg)
    except Exception as e:  # recorded in the outbox already; don't fail the action
        log.warning("SMTP send failed (outbox row kept): %s", e)


def erp_hold(key: str, cp_id: str, reason: str) -> str:
    ref = db.erp_hold_insert(key, cp_id, reason)
    if ref is None:
        return db.erp_hold_get(key)["hold_id"]
    return ref


def slack_alert(key: str, text: str) -> str:
    ref = db.outbox_insert(key, "slack", "#risk-desk", None, text)
    if ref is None:
        return db.outbox_get(key)["outbox_id"]
    url = os.getenv("SLACK_WEBHOOK_URL")
    if url:
        try:
            req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            log.warning("Slack post failed (outbox row kept): %s", e)
    return ref


def raise_watch(key: str, domain: str) -> str:
    existing = db.outbox_get(key)
    if existing:
        return existing["outbox_id"]
    watch_id = watcher.create_watch([domain], os.getenv("WEBHOOK_URL"), every_hours=24)
    ref = db.outbox_insert(key, "watch", domain, f"watch every 24h ({watch_id})", domain)
    return ref or db.outbox_get(key)["outbox_id"]
