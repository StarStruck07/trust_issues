"""Owner: Prachi (P3). STUB from Arth — see PRACHI_product.md section 7."""
from __future__ import annotations

from crd.models import Action


def execute_approved(run_id: str | None = None) -> list[str]:
    return []


def execute_one(action: Action) -> bool:
    raise NotImplementedError


def reconcile() -> None:
    return None
