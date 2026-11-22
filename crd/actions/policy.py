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

from crd.actions import priority
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


def _val(v):
    return getattr(v, "value", v)


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
    # lowercase only a plain leading word ("Headcount changed"), never a name ("Kestrel Home")
    words = text.split(" ", 2)
    if len(words) > 1 and words[1][:1].islower():
        return text[:1].lower() + text[1:]
    return text


def _join(reasons: list[str]) -> str:
    return "; ".join(reasons) if reasons else "changes in our periodic indicators"


def _new_terms(cfg_actions, rating: str, current: int, nt=None) -> int:
    nt = nt if nt is not None else cfg_get(cfg_actions, "new_terms_days")
    fixed = cfg_get(nt, rating) if nt is not None else None
    if fixed is not None and str(fixed).isdigit():
        return int(fixed)
    if rating == "RED":
        return 15
    return max(7, (current or 30) // 2)


# ---------------------------------------------------------------- drafts
def draft_terms_email(cp, a, reasons, cfg) -> tuple[str, str]:
    acts = cfg_get(cfg, "actions")
    terms = cp.credit_terms_days or 30
    new = _new_terms(acts, _rating(a), terms,
                     cfg_get(cfg_get(acts, "customer_terms_email"), "new_terms"))
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
    import re

    why = re.sub(r"\s*\[ev_[A-Za-z0-9]+\]", "", a.explanation or "").strip()
    head = f"{cp.name}: {a.change_since_last}." if a.change_since_last else f"{cp.name} is {rating}."
    return f"{head} {why}".strip()


# ---------------------------------------------------------------- propose
def draft_account_call(cp, a, reasons, pr) -> tuple[str, str]:
    owner = getattr(cp, "account_owner", None) or "account owner"
    subject = f"Call {cp.name} this week — account check-in"
    body = (
        f"For {owner}: please call {cp.name} this week. Rating {_rating(a)}, score {a.score}. "
        f"They rely on us heavily, so a conversation fits better than changing terms. "
        f"Our review noted: {_join(reasons)}. Ask about volumes and payment timing, and note "
        f"anything that changes our view. Priority {pr['tier']} ({pr['reason']})."
    )
    return subject, body


def _inr(v) -> str:
    from crd.report.render import inr

    return inr(v)


def plan(a, cp, cfg) -> list[dict]:
    """Pure: which actions this assessment calls for, with drafts and a one-line why.
    Used by propose() and, at display/approval time, by context()."""
    acts_cfg = cfg_get(cfg, "actions")
    rating = _rating(a)
    exposure = a.exposure_inr or getattr(cp, "exposure_inr", 0) or 0
    role = _val(cp.role)
    reasons, ids = _reasons(a)
    pr = priority.assess(rating, priority.profile(a, cp), cfg)
    imp, dep = pr["importance"], pr["depends_on_us"]
    out: list[dict] = []

    def add(t, status, subject, draft, reversible, why, tag=""):
        out.append({"type": t, "status": status, "subject": subject, "draft": draft,
                    "reversible": reversible, "why": why, "tag": tag, "evidence": ids})

    # internal_alert: rating moved, RED, or needs a person
    if a.change_since_last or rating in ("RED", "CANNOT_VERIFY", "HELD"):
        add(ActionType.internal_alert, ActionStatus.approved, f"Risk desk: {cp.name} {rating}",
            draft_alert(cp, a), True,
            "rating changed" if a.change_since_last else f"{rating.replace('_', ' ')} needs a person")

    # HELD / CANNOT_VERIFY never trigger anything else.
    if rating in ("CANNOT_VERIFY", "HELD"):
        return out

    if rating in ("AMBER", "RED"):
        add(ActionType.raise_watch_frequency, ActionStatus.approved, f"Watch {cp.name} daily",
            cp.domain or cp.name, True, f"{rating}: re-check daily instead of weekly")

    if role == "customer":
        tcfg = cfg_get(acts_cfg, "customer_terms_email")
        min_exp = cfg_get(tcfg, "min_exposure_inr", 1_000_000) if tcfg is not None else 1_000_000
        threshold = min_exp
        if dep == "low":
            threshold = min_exp * float(cfg_get(acts_cfg, "low_dependence_exposure_factor", 0.5))
        soften = (rating == "AMBER" and dep == "high"
                  and cfg_get(acts_cfg, "high_dependence_amber", "account_call") == "account_call")
        if soften:
            sub, body = draft_account_call(cp, a, reasons, pr)
            add(ActionType.internal_alert, ActionStatus.approved, sub, body, True,
                "AMBER, but they depend on us: a call from the account owner instead of a terms change",
                tag="account_call")
        elif tcfg is not None and rating in cfg_get(tcfg, "on", ["AMBER", "RED"]) and exposure >= threshold:
            why = f"{rating} {imp} customer, {_inr(exposure)} exposure ≥ {_inr(threshold)}"
            if dep == "low":
                why += " (lower bar: they can switch easily)"
            sub, body = draft_terms_email(cp, a, reasons, cfg)
            add(ActionType.customer_terms_email, ActionStatus.pending_approval, sub, body, False, why)
        hcfg = cfg_get(acts_cfg, "credit_hold")
        if hcfg is not None and rating in cfg_get(hcfg, "on", ["RED"]):
            sub, body = draft_credit_hold(cp, a, reasons, ids)
            add(ActionType.credit_hold, ActionStatus.pending_approval, sub, body, False,
                "RED customer: stop new credit until a person reviews"
                + ("; hard stop " + a.hard_stop.replace("_", " ") if a.hard_stop else ""))
        gcfg = cfg_get(acts_cfg, "guarantee_request")
        if gcfg is not None and rating == "RED" and exposure >= 2 * min_exp:
            sub, body = draft_guarantee(cp, a, reasons)
            add(ActionType.guarantee_request, ActionStatus.proposed, sub, body, False,
                f"RED with {_inr(exposure)} exposure ≥ {_inr(2 * min_exp)}: draft only")

    if role in SUPPLIER_ROLES:
        rcfg = cfg_get(acts_cfg, "backup_rfq")
        crit_ok = cp.criticality in cfg_get(rcfg, "only_criticality", ["sole_source", "primary"]) if rcfg else False
        if rcfg is not None and rating in cfg_get(rcfg, "on", ["AMBER", "RED"]) and (crit_ok or imp == "strategic"):
            crit = (cp.criticality or "").replace("_", " ")
            sub, body = draft_backup_rfq(cp, a, cfg)
            add(ActionType.backup_rfq, ActionStatus.pending_approval, sub, body, False,
                f"{rating} {crit if crit and crit != 'na' else imp} supplier: line up a backup before it fails")
    return out


def _senior_needed(pr: dict, reversible: bool, cfg) -> bool:
    senior_for = cfg_get(cfg_get(cfg, "actions"), "senior_approval_for", ["strategic"]) or []
    return (not reversible) and pr["importance"] in senior_for


def propose(a, cp, cfg, review_id: str) -> list:
    inbox = os.getenv("TEAM_INBOX", "team-inbox@example.com")
    out = []
    for spec in plan(a, cp, cfg):
        t = spec["type"]
        key = f"{cp.cp_id}:{t.value}:{review_id}" + (f":{spec['tag']}" if spec["tag"] else "")
        act = Action(
            action_id="act_" + uuid.uuid4().hex[:8], run_id=a.run_id, cp_id=cp.cp_id, type=t,
            idempotency_key=key, reversible=spec["reversible"], status=spec["status"], to=inbox,
            subject=spec["subject"], draft=spec["draft"],
            draft_hash=sha256(spec["draft"]) if spec["status"] == ActionStatus.approved else "",
            reason_evidence=spec["evidence"],
        )
        if not db.insert_action(act):
            existing = _existing(key, a.run_id)
            if existing is not None:
                act = existing
        out.append(act)
    return out


def _counterparty(cp_id: str):
    getter = getattr(db, "get_counterparty", None)
    try:
        return getter(cp_id) if getter else None
    except Exception:
        return None


def context(act, a=None, cp=None, cfg=None) -> dict:
    """Decision context for one stored action: {priority, label, why, requires_senior}.
    Recomputed from the same rules as propose(), so nothing extra needs storing."""
    from crd.deps import config

    cfg = cfg or config.get()
    if a is None:
        try:
            a = db.get_assessment(act.run_id, act.cp_id)
        except Exception:
            a = None
    cp = cp or _counterparty(act.cp_id)
    if a is None:
        return {"priority": None, "label": "", "why": "", "requires_senior": False}
    pr = priority.assess(a.rating, priority.profile(a, cp), cfg)
    why = ""
    if cp is not None:
        tag = "account_call" if (act.idempotency_key or "").endswith(":account_call") else ""
        for spec in plan(a, cp, cfg):
            if _val(spec["type"]) == _val(act.type) and spec["tag"] == tag:
                why = spec["why"]
                break
    return {"priority": pr["tier"], "label": pr["label"], "why": why,
            "requires_senior": _senior_needed(pr, act.reversible, cfg)}


def _existing(key: str, run_id: str):
    getter = getattr(db, "get_action_by_key", None)
    if getter:
        return getter(key)
    for act in db.get_actions(None, None):
        if act.idempotency_key == key:
            return act
    return None
