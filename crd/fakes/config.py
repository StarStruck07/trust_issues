"""Stand-in for crd/config.py (Arth). Attribute-style access like a parsed config.yaml."""
from __future__ import annotations

from types import SimpleNamespace as NS

_CFG = NS(
    client_name="Our 3PL",
    report=NS(
        fields=["priority", "display_name", "role", "rating", "score", "inr_at_risk", "top_reason",
                "sources", "action_status"],
    ),
    actions=NS(
        customer_terms_email=NS(on=["AMBER", "RED"], min_exposure_inr=1_000_000),
        credit_hold=NS(on=["RED"]),
        backup_rfq=NS(on=["AMBER", "RED"], only_criticality=["sole_source", "primary"]),
        guarantee_request=NS(on=["RED"]),
        new_terms_days=NS(RED=15),  # AMBER: half the current terms
        # customers who depend on us heavily get an account-owner call instead of a terms email on AMBER
        high_dependence_amber="account_call",
        # customers who can drop us easily: terms email from this fraction of min_exposure_inr
        low_dependence_exposure_factor=0.5,
        # irreversible actions on these accounts need a senior approver
        senior_approval_for=["strategic"],
    ),
    priority=NS(
        severity=NS(RED=3, AMBER=2, HELD=1.5, CANNOT_VERIFY=1.5, GREEN=0),
        importance=NS(strategic=3, core=2, standard=1),
        dependence_shift=NS(high=-1, medium=0, low=1, na=0),
        tiers=NS(P1=6, P2=4, P3=2),  # urgency >= threshold
    ),
)


def get():
    return _CFG


def review_id(as_of: str) -> str:
    """One review per ISO week, so re-runs in the same week share idempotency keys."""
    from datetime import date

    y, w, _ = date.fromisoformat(as_of).isocalendar()
    return f"rv_{y}W{w:02d}"
