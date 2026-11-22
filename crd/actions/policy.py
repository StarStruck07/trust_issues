"""Turn one Assessment into proposed Actions (PRACHI_product.md section 6).

Rules come from ``cfg.actions``. Irreversible actions always start in
``pending_approval``; HELD / CANNOT_VERIFY only ever produce an internal alert.
Idempotency key = ``{cp_id}:{type}:{review_id}``, so re-runs and resumes never
create a second action for the same counterparty in the same review.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import date, timedelta

from crd.deps import cfg_get, db, models

Action = models.Action
ActionStatus = models.ActionStatus
ActionType = models.ActionType

SUPPLIER_ROLES = {"carrier", "vendor", "warehouse_lessor"}
IRREVERSIBLE = {
    ActionType.customer_terms_email, ActionType.credit_hold, ActionType.backup_rfq,
    ActionType.guarantee_request,
}
HUMAN_LABEL = {
    "internal_alert": "Internal alert to the credit team",
    "raise_watch_frequency": "Watch this counterparty daily",
    "customer_terms_email": "Email customer about credit terms",
    "credit_hold": "Place credit hold",
    "backup_rfq": "Request quote from backup carrier",
    "guarantee_request": "Draft a guarantee request (never sent)",
}


def sha256(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _rating(a) -> str:
    return getattr(a.rating, "value", a.rating)


def _reasons(a, n: int = 2) -> tuple[list[str], list[str]]:
    """Plain-language notes and evidence ids of the ``n`` most negative signals."""
    neg = sorted((s for s in a.signals if s.points < 0), key=lambda s: s.points)[:n]
    ids: list[str] = []
    for s in neg:
        ids += s.evidence_ids[:1]
    claims = {e.evidence_id: e.claim for e in db.get_evidence_by_ids(ids)} if ids else {}
    notes = []
    for s in neg:
        eid = s.evidence_ids[0] if s.evidence_ids else None
        notes.append(_plain(claims.get(eid) or s.note))
    return notes, ids


def _plain(text: str) -> str:
    text = (text or "").strip().rstrip(".")
    return text[:1].lower() + text[1:] if text else text


def _join(reasons: list[str]) -> str:
    return "; ".join(reasons) if reasons else "changes in our periodic indicators"


def _new_terms(cfg_actions, rating: str, current: int) -> int:
    nt = cfg_get(cfg_actions, "new_terms_days")
    fixed = cfg_get(nt, rating) if nt is not None else None
    if fixed is not None:
        return int(fixed)
    if rating == "RED":
        return 15
    return max(7, (current or 30) // 2)


# ---------------------------------------------------------------- drafts
def draft_terms_email(cp, a, reasons, cfg) -> tuple[str, str]:
    acts = cfg_get(cfg, "actions")
    terms = cp.credit_terms_days or 30
    new = _new_terms(acts, _rating(a), terms)
    start = (date.today() + timedelta(days=7)).strftime("%d %b %Y")
    client = cfg_get(cfg, "client_name", "our company")
    subject = f"Review of credit terms — {cp.name}"
    body = (
        f"Dear {cp.name} team,\n\n"
        f"Following our periodic account review we would like to discuss moving terms on new "
        f"shipments from {terms} to {new} days from {start}. Our review noted: {_join(reasons)}.\n\n"
        f"Existing invoices are unaffected. Could we set up a call this week?\n\n"
        f"— Credit team, {client}"
    )
    return subject, body


def draft_credit_hold(cp, a, reasons, ids) -> tuple[str, str]:
    subject = f"Credit hold — {cp.name} ({cp.cp_id})"
    body = (
        f"Hold new credit shipments for {cp.name} ({cp.cp_id}). Rating {_rating(a)}, score {a.score}. "
        f"Reasons: {_join(reasons)}. Evidence: {', '.join(ids) or 'see review'}. "
        f"Review by {cp.account_owner or 'credit-team'}."
    )
    return subject, body


def draft_backup_rfq(cp, a, cfg) -> tuple[str, str]:
    client = cfg_get(cfg, "client_name", "our company")
    alts = getattr(cp, "alt_suppliers", None) or ["<backup supplier 1>", "<backup supplier 2>"]
    subject = f"Rate request — lanes currently served by {cp.name}"
    body = (
        "Hello,\n\n"
        f"We are reviewing capacity on lanes currently served by {cp.name} and would welcome a "
        "rate quote for comparable volumes over the next quarter. Please share lane-wise rates, "
        "transit times and available capacity.\n\n"
        f"To be sent to: {', '.join(alts)}\n\n"
        f"— Procurement team, {client}"
    )
    return subject, body


def draft_guarantee(cp, a, reasons) -> tuple[str, str]:
    subject = f"Request for security on open balance — {cp.name}"
    body = (
        f"Dear {cp.name} team,\n\nGiven the size of the open balance and our recent review "
        f"({_join(reasons)}), we would like to discuss a bank guarantee or advance payment for new "
        "shipments. We are happy to talk through options.\n\n— Credit team"
    )
    return subject, body


def draft_alert(cp, a) -> str:
    rating = _rating(a)
    if rating in ("CANNOT_VERIFY", "HELD"):
        missing = "; ".join(a.cannot_verify) or "identity or a required source"
        return f"{cp.name} needs a person: {missing}"
    move = a.change_since_last or f"→ {rating}"
    return f"{cp.name}: {move}. {a.explanation}".strip()


# ---------------------------------------------------------------- propose
def propose(a, cp, cfg, review_id: str) -> list:
    acts_cfg = cfg_get(cfg, "actions")
    rating = _rating(a)
    exposure = a.exposure_inr or getattr(cp, "exposure_inr", 0) or 0
    role = cp.role
    reasons, ids = _reasons(a)
    inbox = os.getenv("TEAM_INBOX", "team-inbox@example.com")
    out = []

    def add(t, status, subject, draft, reversible):
        key = f"{cp.cp_id}:{t.value}:{review_id}"
        act = Action(
            action_id="act_" + uuid.uuid4().hex[:8], run_id=a.run_id, cp_id=cp.cp_id, type=t,
            idempotency_key=key, reversible=reversible, status=status, to=inbox,
            subject=subject, draft=draft,
            draft_hash=sha256(draft) if status == ActionStatus.approved else None,
            reason_evidence=ids,
        )
        if not db.insert_action(act):
            existing = _existing(key, a.run_id)
            if existing is not None:
                act = existing
        out.append(act)

    # internal_alert: rating moved, RED, or needs a person
    if a.change_since_last or rating in ("RED", "CANNOT_VERIFY", "HELD"):
        add(ActionType.internal_alert, ActionStatus.approved,
            f"Risk desk: {cp.name} {rating}", draft_alert(cp, a), True)

    # HELD / CANNOT_VERIFY never trigger anything else.
    if rating in ("CANNOT_VERIFY", "HELD"):
        return out

    if rating in ("AMBER", "RED"):
        add(ActionType.raise_watch_frequency, ActionStatus.approved,
            f"Watch {cp.name} daily", cp.domain or cp.name, True)

    if role == "customer":
        tcfg = cfg_get(acts_cfg, "customer_terms_email")
        if tcfg is not None and rating in cfg_get(tcfg, "on", ["AMBER", "RED"]) \
                and exposure >= cfg_get(tcfg, "min_exposure_inr", 1_000_000):
            s, b = draft_terms_email(cp, a, reasons, cfg)
            add(ActionType.customer_terms_email, ActionStatus.pending_approval, s, b, False)
        hcfg = cfg_get(acts_cfg, "credit_hold")
        if hcfg is not None and rating in cfg_get(hcfg, "on", ["RED"]):
            s, b = draft_credit_hold(cp, a, reasons, ids)
            add(ActionType.credit_hold, ActionStatus.pending_approval, s, b, False)
        gcfg = cfg_get(acts_cfg, "guarantee_request")
        min_exp = cfg_get(tcfg, "min_exposure_inr", 1_000_000) if tcfg is not None else 1_000_000
        if gcfg is not None and rating == "RED" and exposure >= 2 * min_exp:
            s, b = draft_guarantee(cp, a, reasons)
            add(ActionType.guarantee_request, ActionStatus.proposed, s, b, False)

    if role in SUPPLIER_ROLES:
        rcfg = cfg_get(acts_cfg, "backup_rfq")
        if rcfg is not None and rating in cfg_get(rcfg, "on", ["AMBER", "RED"]) \
                and cp.criticality in cfg_get(rcfg, "only_criticality", ["sole_source", "primary"]):
            s, b = draft_backup_rfq(cp, a, cfg)
            add(ActionType.backup_rfq, ActionStatus.pending_approval, s, b, False)

    return out


def _existing(key: str, run_id: str):
    getter = getattr(db, "get_action_by_key", None)
    if getter:
        return getter(key)
    for act in db.get_actions(None, None):
        if act.idempotency_key == key:
            return act
    return None
