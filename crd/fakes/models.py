"""Stand-in for crd/models.py (Arth). Shapes from PRACHI_product.md section 11."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Rating(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    CANNOT_VERIFY = "CANNOT_VERIFY"
    HELD = "HELD"


class Counterparty(BaseModel):
    cp_id: str
    name: str
    legal_name: str | None = None
    domain: str | None = None
    role: str = "customer"  # carrier | warehouse_lessor | vendor | customer
    criticality: str = "na"  # sole_source | primary | backup | na
    exposure_inr: float = 0
    credit_terms_days: int = 0
    account_owner: str = "credit-team"
    alt_suppliers: list[str] = Field(default_factory=list)
    # how much the relationship matters to us: strategic | core | standard
    importance: str = "standard"
    # customers only: how much THEY depend on us (high = we are hard to replace) | na
    depends_on_us: str = "na"


class ResolvedEntity(BaseModel):
    match_type: str = "exact"  # exact | fuzzy | ambiguous | none
    confidence: float = 1.0
    legal_name: str | None = None
    domain: str | None = None
    cin: str | None = None
    candidates: list[dict] = Field(default_factory=list)


class SignalScore(BaseModel):
    signal: str
    points: int
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class Assessment(BaseModel):
    run_id: str
    cp_ids: list[str]
    display_name: str
    role: str
    entity: ResolvedEntity = Field(default_factory=ResolvedEntity)
    rating: Rating
    score: int
    hard_stop: str | None = None
    signals: list[SignalScore] = Field(default_factory=list)
    cannot_verify: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    change_since_last: str | None = None
    exposure_inr: float = 0
    inr_at_risk: float = 0
    explanation: str = ""
    action_ids: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class Evidence(BaseModel):
    evidence_id: str
    run_id: str
    cp_id: str
    signal: str
    claim: str
    value: object = None
    status: str = "ok"  # ok | empty | unavailable
    source_type: str = "crustdata"
    source: str = ""
    field_path: str | None = None
    passage: str | None = None
    url: str | None = None
    raw_ref: str | None = None
    record_date: str | None = None
    retrieved_at: str | None = None
    stale: bool = False


class ActionType(str, Enum):
    internal_alert = "internal_alert"
    raise_watch_frequency = "raise_watch_frequency"
    customer_terms_email = "customer_terms_email"
    credit_hold = "credit_hold"
    backup_rfq = "backup_rfq"
    guarantee_request = "guarantee_request"


class ActionStatus(str, Enum):
    proposed = "proposed"
    pending_approval = "pending_approval"
    approved = "approved"
    executing = "executing"
    done = "done"
    rejected = "rejected"
    held = "held"
    failed = "failed"


class Action(BaseModel):
    action_id: str
    run_id: str
    cp_id: str
    type: ActionType
    idempotency_key: str
    reversible: bool
    status: ActionStatus
    to: str | None = None
    subject: str | None = None
    draft: str = ""
    draft_hash: str | None = None
    reason_evidence: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None
    external_ref: str | None = None
    error: str | None = None
    # decision context (requested from Arth's model): P1..P4, why this action, senior sign-off
    priority: str | None = None
    why: str | None = None
    requires_senior: bool = False


class LedgerEntry(BaseModel):
    call_id: str
    run_id: str
    provider: str
    endpoint: str
    request_hash: str = ""
    credits: float = 0
    inr: float = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    status: str = "ok"  # ok | cached | error
    note: str = ""
    ts: str = ""
