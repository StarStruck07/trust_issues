"""Owner: Nilay (P2). STUB from Arth — see NILAY_data.md section 10."""
from __future__ import annotations

from datetime import date

from crd.models import Evidence, ResolvedEntity


def leadership_exits(entity: ResolvedEntity, enrich_resp: dict, client, cfg, as_of: date) -> list[Evidence]:
    raise NotImplementedError


def purge_people_raw(run_id: str) -> None:
    """Delete var/runs/<run_id>/raw/person_enrich_* after a run (data minimisation)."""
    return None
