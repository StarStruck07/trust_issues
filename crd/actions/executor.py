"""Exactly-once execution of approved actions (PRACHI_product.md section 7.2).

State machine: approved -> executing -> done | failed. Every move is a
compare-and-set (``db.transition_action``), so two workers — threads or
processes — can never both own one action. Channels are keyed by the
idempotency key, and ``reconcile()`` on startup settles anything a crash left in
``executing`` by looking for the channel record.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from crd.actions import channels
from crd.actions.policy import IRREVERSIBLE, sha256
from crd.deps import db, models

log = logging.getLogger("crd.executor")
ActionType = models.ActionType
_lock = threading.Lock()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _type(a) -> ActionType:
    return a.type if isinstance(a.type, ActionType) else ActionType(a.type)


def execute_approved(run_id: str | None = None) -> list[str]:
    """Execute every approved action (optionally for one run). Returns ids that finished."""
    done = []
    with _lock:
        for a in db.get_actions("approved", run_id):
            if execute_one(a):
                done.append(a.action_id)
    return done


def execute_one(a) -> bool:
    t = _type(a)
    if t == ActionType.guarantee_request:
        return False  # draft only, never executed
    if not db.transition_action(a.action_id, "approved", "executing"):
        return False  # someone else has it, or it changed
    if t in IRREVERSIBLE and sha256(a.draft) != a.draft_hash:
        db.transition_action(a.action_id, "executing", "pending_approval",
                             error="draft changed after approval")
        return False
    slow = int(os.getenv("CRD_SLOW_EXECUTE", "0") or 0)
    if slow > 0 and t in IRREVERSIBLE:  # demo kill window, only where it matters
        log.warning("CRD_SLOW_EXECUTE: sleeping %ss before %s", slow, a.action_id)
        time.sleep(slow)
    try:
        ref = _dispatch(t, a)
    except Exception as e:
        log.exception("action %s failed", a.action_id)
        db.transition_action(a.action_id, "executing", "failed", error=str(e)[:300])
        return False
    return db.transition_action(a.action_id, "executing", "done", external_ref=ref,
                                executed_at=_now(), error=None)


def _dispatch(t: ActionType, a) -> str:
    key = a.idempotency_key
    if t in (ActionType.customer_terms_email, ActionType.backup_rfq):
        return channels.send_email(key, a.to, a.subject, a.draft)
    if t == ActionType.credit_hold:
        return channels.erp_hold(key, a.cp_id, a.draft)
    if t == ActionType.internal_alert:
        return channels.slack_alert(key, a.draft)
    if t == ActionType.raise_watch_frequency:
        return channels.raise_watch(key, a.draft)
    raise ValueError(f"no channel for {t}")


def _channel_record(a) -> str | None:
    if _type(a) == ActionType.credit_hold:
        r = db.erp_hold_get(a.idempotency_key)
        return r["hold_id"] if r else None
    r = db.outbox_get(a.idempotency_key)
    return r["outbox_id"] if r else None


def reconcile() -> None:
    """Run on every startup, before anything else executes."""
    for a in db.get_actions("executing"):
        ref = _channel_record(a)
        if ref:
            db.transition_action(a.action_id, "executing", "done", external_ref=ref,
                                 executed_at=a.executed_at or _now(), note="reconciled after restart")
            log.info("reconcile: %s already written (%s) -> done", a.action_id, ref)
        else:
            db.transition_action(a.action_id, "executing", "approved")
            log.info("reconcile: %s not written -> approved", a.action_id)


def retry(action_id: str) -> bool:
    """UI 'Retry' button on a failed action: back to approved, then execute once."""
    if not db.transition_action(action_id, "failed", "approved", error=None):
        return False
    threading.Thread(target=execute_approved, daemon=True).start()
    return True
