"""Data contracts shared by every module. Owner: Arth (P1). Frozen at 13:45 — add optional fields only."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel


class Role(str, Enum):
    carrier = "carrier"
    warehouse_lessor = "warehouse_lessor"
    vendor = "vendor"
    customer = "customer"


SUPPLIER_ROLES = {Role.carrier, Role.warehouse_lessor, Role.vendor}


class Rating(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    CANNOT_VERIFY = "CANNOT_VERIFY"
    HELD = "HELD"


class Counterparty(BaseModel):
    cp_id: str
    name: str
    legal_name: Optional[str] = None
    domain: Optional[str] = None
    cin: Optional[str] = None
    gstin: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    role: Role
    criticality: Literal["sole_source", "primary", "backup", "na"] = "na"
    exposure_inr: float = 0
    credit_terms_days: Optional[int] = None
    overdue_inr: Optional[float] = None
    dso_days: Optional[float] = None
    account_owner: str = "credit-team"
    alt_suppliers: list[str] = []
    record_hash: str = ""


class ReviewRequest(BaseModel):
    raw_text: str
    scope: Literal["all", "suppliers", "customers", "ids"] = "all"
    cp_ids: list[str] = []
    focus: Optional[str] = None
    as_of: date
    trigger: Literal["manual", "watcher", "schedule"] = "manual"
    clarification: Optional[str] = None
    input_path: Optional[str] = None


class ResolvedEntity(BaseModel):
    cp_ids: list[str]
    status: Literal["resolved", "ambiguous", "not_found"]
    crustdata_company_id: Optional[int] = None
    legal_name: Optional[str] = None
    domain: Optional[str] = None
    match_type: Optional[Literal["domain", "cin", "gstin", "name"]] = None
    confidence: float = 0.0
    candidates: list[dict[str, Any]] = []
    evidence_ids: list[str] = []


SignalName = Literal[
    "identity", "legal_status", "insolvency",
    "headcount_qoq", "headcount_6m", "role_mix", "hiring",
    "leadership_exit", "funding_runway", "news", "employee_reviews",
    "web_traffic", "payment_behaviour", "positive",
]


class Evidence(BaseModel):
    evidence_id: str
    run_id: str
    cp_id: str
    signal: SignalName
    claim: str
    value: Any = None
    status: Literal["ok", "empty", "unavailable"] = "ok"
    source_type: Literal["crustdata_api", "registry", "web", "client_record"]
    source: str
    field_path: Optional[str] = None
    passage: Optional[str] = None
    url: Optional[str] = None
    raw_ref: Optional[str] = None
    record_date: Optional[date] = None
    retrieved_at: datetime
    stale: bool = False


class SignalScore(BaseModel):
    signal: SignalName
    points: int
    evidence_ids: list[str]
    note: str = ""


class Assessment(BaseModel):
    run_id: str
    cp_ids: list[str]
    display_name: str
    role: Role
    entity: ResolvedEntity
    rating: Rating
    score: int
    hard_stop: Optional[str] = None
    signals: list[SignalScore] = []
    cannot_verify: list[str] = []
    conflicts: list[str] = []
    change_since_last: Optional[str] = None
    exposure_inr: float = 0
    inr_at_risk: float = 0
    explanation: str = ""
    action_ids: list[str] = []
    extra: dict[str, Any] = {}


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
    to: Optional[str] = None
    subject: Optional[str] = None
    draft: str = ""
    draft_hash: str = ""
    reason_evidence: list[str] = []
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    external_ref: Optional[str] = None
    error: Optional[str] = None


class LedgerEntry(BaseModel):
    call_id: str
    run_id: str
    provider: Literal["crustdata", "anthropic", "registry", "web"]
    endpoint: str
    request_hash: str
    credits: float = 0.0
    inr: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    status: Literal["ok", "cached", "error", "rate_limited", "timeout"]
    note: str = ""
    ts: datetime
