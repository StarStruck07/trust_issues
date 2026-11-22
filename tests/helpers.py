"""Test helpers (Arth): build Evidence by hand; a fake Crustdata client for core tests."""
import uuid
from datetime import date, datetime, timezone

from crd.models import Counterparty, Evidence, ResolvedEntity


def ev(signal, value=None, status="ok", stale=False, claim=None, **kw):
    return Evidence(evidence_id=kw.pop("evidence_id", f"ev_{uuid.uuid4().hex[:8]}"), run_id="run_t", cp_id="C-1",
                    signal=signal, claim=claim or f"{signal} {value}", value=value, status=status,
                    source_type=kw.pop("source_type", "crustdata_api"), source=kw.pop("source", "test"),
                    field_path=kw.pop("field_path", signal), record_date=kw.pop("record_date", date(2026, 11, 15)),
                    retrieved_at=datetime.now(timezone.utc), stale=stale, **kw)


def cp(cp_id="C-1", name="Acme Logistics", role="carrier", **kw):
    return Counterparty(cp_id=cp_id, name=name, role=role, **kw)


def resolved(cp_ids=("C-1",), **kw):
    return ResolvedEntity(cp_ids=list(cp_ids), status="resolved", crustdata_company_id=kw.pop("cid", 1),
                          legal_name=kw.pop("legal_name", "Acme Logistics Private Limited"),
                          domain=kw.pop("domain", "acme.in"), match_type="domain", confidence=1.0, **kw)


class FakeClient:
    """identify() answers from a dict: domain/name -> list of (name, domain, confidence, id, location)."""

    def __init__(self, table=None, fail=False):
        self.table = table or {}
        self.fail = fail
        self.last_raw_ref = "var/runs/test/raw/identify.json"
        self.calls = []

    def identify(self, *, domains=None, names=None):
        from crd.data.errors import SourceUnavailable
        self.calls.append(("identify", domains, names))
        if self.fail:
            raise SourceUnavailable("timeout x3")
        key = (domains or names)[0]
        ms = self.table.get(key, [])
        return [{"matched_on": key, "matches": [
            {"confidence_score": c, "company_data": {"crustdata_company_id": i,
             "basic_info": {"name": n, "primary_domain": d, "location": loc}}} for (n, d, c, i, loc) in ms]}]
