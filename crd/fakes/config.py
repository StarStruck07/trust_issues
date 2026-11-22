"""Stand-in for crd/config.py (Arth). Attribute-style access like a parsed config.yaml."""
from __future__ import annotations

from types import SimpleNamespace as NS

_CFG = NS(
    client_name="Our 3PL",
    report=NS(
        fields=["display_name", "role", "rating", "score", "inr_at_risk", "top_reason",
                "sources", "action_status"],
    ),
    actions=NS(
        customer_terms_email=NS(on=["AMBER", "RED"], min_exposure_inr=1_000_000),
        credit_hold=NS(on=["RED"]),
        backup_rfq=NS(on=["AMBER", "RED"], only_criticality=["sole_source", "primary"]),
        guarantee_request=NS(on=["RED"]),
        new_terms_days=NS(RED=15),  # AMBER: half the current terms
    ),
)


def get():
    return _CFG


def review_id(as_of: str) -> str:
    """One review per ISO week, so re-runs in the same week share idempotency keys."""
    from datetime import date

    y, w, _ = date.fromisoformat(as_of).isocalendar()
    return f"rv_{y}W{w:02d}"
