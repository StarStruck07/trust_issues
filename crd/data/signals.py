"""Owner: Nilay (P2). STUB from Arth — see NILAY_data.md section 7."""
from __future__ import annotations

from crd.models import Counterparty, Evidence, ResolvedEntity


def gather(cp: Counterparty, entity: ResolvedEntity, client, cfg) -> list[Evidence]:
    """Never raises for a single source; failed sources become Evidence(status='unavailable')."""
    raise NotImplementedError
