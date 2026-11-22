"""Owner: Nilay (P2). STUB from Arth — see NILAY_data.md section 9."""
from __future__ import annotations

from crd.models import Counterparty, Evidence, ResolvedEntity


def check(entity: ResolvedEntity, cp: Counterparty, client, cfg) -> list[Evidence]:
    raise NotImplementedError


def lookup_by_id(cin: str | None = None, gstin: str | None = None, client=None) -> dict | None:
    """Returns {"legal_name", "status", "source_url"} or None. Used by resolve.py."""
    return None
