"""Owner: Prachi (P3). STUB from Arth — see PRACHI_product.md section 6."""
from __future__ import annotations

from crd.models import Action, Assessment, Counterparty


def propose(a: Assessment, cp: Counterparty, cfg, review_id: str) -> list[Action]:
    """Build actions, db.insert_action each (idempotent), return them. Stub proposes nothing."""
    return []
