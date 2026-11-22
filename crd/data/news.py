"""Owner: Nilay (P2). STUB from Arth — see NILAY_data.md section 8."""
from __future__ import annotations

from crd.models import Counterparty, Evidence, ResolvedEntity


def collect(entity: ResolvedEntity, cp: Counterparty, enrich_resp: dict, client, cfg) -> list[Evidence]:
    raise NotImplementedError
