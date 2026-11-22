# Arth — P1 Core (lead and integrator)

> Counterparty Risk Desk · ContextCon 05 · BITS Pilani · 22 Nov 2026
> Teammates: **Nilay** (P2 Data) · **Prachi** (P3 Product)
> Shared rules: `CLAUDE.md` · Team overview: `docs/TEAM_PLAN.md` · This file: `docs/ARTH_core.md`

---

## 1. Your role in one paragraph

You build the **engine** and you **own integration**. You write the contracts the other two code against (`models.py`, `db.py`, `config.py`, `config.yaml`, the repo skeleton) and push them by **13:45**: until then Nilay and Prachi are guessing. Then you build:

- identity resolution and dedupe;
- the scoring rules and coverage gate, which decide GREEN / AMBER / RED / CANNOT VERIFY / HELD;
- the orchestrator, with step checkpoints so a `kill -9` resumes cleanly;
- the request parser and the explanation writer;
- the CLI and the test-case runner.

You merge everyone's branches into `main` at each sync point, and you make the final scope calls.

**What the judges test that depends on you:** correctness on the hidden test set, *Cannot verify* instead of wrong answers, identical answers for reworded requests, resuming after a kill, and the live fix (a config change, if you build it right).

---

## 2. Your timeline

| Time | Task | Section | Unblocks |
|---|---|---|---|
| 13:20–13:45 | Repo skeleton, `models.py`, `db.py`, `config.py`, `config.yaml`, `requirements.txt`, `.env.example`, `.gitignore` → push to `main`, post "contracts pushed" | 4 | Nilay, Prachi |
| 13:45–14:00 | Input adapter for the kickoff-kit format | 5 | everything |
| 13:50–14:15 | `resolve.py` | 6 | scoring |
| 14:00–14:20 | `scoring.py` + `tests/test_scoring.py` | 7 | Prachi's pages |
| 14:20 | **Sync:** one counterparty end to end in mock mode | 8 | checkpoint |
| 14:20–14:30 | Merge `p2-data` and `p3-product` into `main`; Prachi submits the checkpoint | 12 | |
| 14:30–15:30 | Orchestrator: checkpoints, resume, budget, async + progress | 8 | Prachi's progress page |
| 15:00–15:45 | `request_parser.py`, `explain.py` | 9, 10 | |
| 15:30 | **Sync:** live mode on 5 counterparties | | |
| 15:30–16:30 | `cli.py` complete, `tests/run_cases.py`, determinism test, kill test | 11 | |
| 16:30 | **Sync:** full test-case run; 10-minute triage with the team | | |
| 16:30–17:15 | Fix wrong cases, tune `config.yaml` | 7 | |
| 17:15 | **Feature freeze** | | |
| 17:15–17:45 | Rehearse the kill demo with Prachi; final test run | 13 | |
| 17:45–18:00 | Final merge, `git tag v1`, check that the repo is public | 12 | |

**The hour-3 requirement change was due at about 13:15.** Find out what it was (WhatsApp group or announcements), and decide where it lands: usually a `config.yaml` key, a new `Assessment` field, or a new report column. Tell Prachi if it touches the report.

---

## 3. Files you own

```
crd/__init__.py
crd/models.py           crd/config.py          crd/db.py
crd/orchestrator.py     crd/resolve.py         crd/scoring.py
crd/explain.py          crd/request_parser.py  crd/cli.py
crd/inputs.py           (input adapter)
config.yaml   requirements.txt   .env.example   .gitignore
tests/test_scoring.py   tests/test_resolve.py  tests/run_cases.py
data/input/*            (kickoff-kit input files)
```

Also create empty stubs, with only the signatures and `raise NotImplementedError`, for the files the others own, so imports work from minute one:

- **Nilay:** `crd/llm.py`, `crd/data/{errors,crustdata,signals,news,registries,people,watcher}.py`
- **Prachi:** `crd/actions/{policy,executor,channels}.py`, `crd/report/render.py`, `crd/web/app.py`

---

## 4. Contracts: write these exactly, push by 13:45

### 4.1 `requirements.txt`
```
fastapi
uvicorn[standard]
jinja2
python-multipart
httpx
pydantic>=2
pyyaml
typer
python-dotenv
anthropic
tenacity
pytest
```

### 4.2 `.env.example`
```
CRUSTDATA_API_KEY=
ANTHROPIC_API_KEY=
CRD_MOCK=1
CRD_DB_PATH=var/crd.db
SLACK_WEBHOOK_URL=
TEAM_INBOX=team@example.com
CRD_SLOW_EXECUTE=0
```

### 4.3 `.gitignore`
```
.env
var/
__pycache__/
*.pyc
.venv/
```

### 4.4 `crd/models.py`

Copy this verbatim. Nilay and Prachi depend on every name in it.

```python
from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel

class Role(str, Enum):
    carrier = "carrier"; warehouse_lessor = "warehouse_lessor"; vendor = "vendor"; customer = "customer"

class Rating(str, Enum):
    GREEN = "GREEN"; AMBER = "AMBER"; RED = "RED"; CANNOT_VERIFY = "CANNOT_VERIFY"; HELD = "HELD"

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
    clarification: Optional[str] = None     # set when the parser cannot match a named counterparty

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
    extra: dict[str, Any] = {}       # room for the hour-3 change / live fix without breaking anyone

class ActionType(str, Enum):
    internal_alert = "internal_alert"
    raise_watch_frequency = "raise_watch_frequency"
    customer_terms_email = "customer_terms_email"
    credit_hold = "credit_hold"
    backup_rfq = "backup_rfq"
    guarantee_request = "guarantee_request"

class ActionStatus(str, Enum):
    proposed = "proposed"; pending_approval = "pending_approval"; approved = "approved"
    executing = "executing"; done = "done"; rejected = "rejected"; held = "held"; failed = "failed"

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
```

### 4.5 `crd/db.py`

Use `sqlite3` with WAL mode, one connection per call, and `BEGIN IMMEDIATE` for writes.

```python
import json, os, sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from crd.models import *

DB_PATH = os.getenv("CRD_DB_PATH", "var/crd.db")
def now(): return datetime.now(timezone.utc).isoformat()

@contextmanager
def conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=30000")
    try: yield c
    finally: c.close()

SCHEMA = """ ...the tables in 4.6... """
```

Every function below must exist with exactly this signature:

```python
def init_db() -> None
def new_run(req: ReviewRequest) -> str                       # "run_" + 8 hex; status "running"
def finish_run(run_id: str, status: str) -> None             # done | partial | failed
def get_run(run_id: str) -> dict | None
def list_runs(limit: int = 20) -> list[dict]
def last_unfinished_run() -> str | None
def upsert_counterparty(cp: Counterparty) -> bool            # True if new or changed hash
def get_counterparty(cp_id: str) -> Counterparty | None
def list_counterparties() -> list[Counterparty]
def step_status(run_id, cp_id, step) -> str | None
def mark_step(run_id, cp_id, step, status, output: dict | None = None) -> None
def get_step_output(run_id, cp_id, step) -> dict | None
def list_steps(run_id) -> list[dict]
def save_evidence(ev: list[Evidence]) -> None                # INSERT OR REPLACE on evidence_id
def get_evidence(run_id, cp_id) -> list[Evidence]
def get_evidence_by_ids(ids: list[str]) -> list[Evidence]
def save_assessment(a: Assessment) -> None                   # keyed (run_id, cp_ids[0])
def get_assessment(run_id, cp_id) -> Assessment | None
def get_assessments(run_id) -> list[Assessment]
def previous_rating(cp_id, before_run_id) -> str | None
def insert_action(a: Action) -> bool                         # INSERT OR IGNORE; False when the key exists
def get_action(action_id) -> Action | None
def get_actions(status: str | None = None, run_id: str | None = None) -> list[Action]
def transition_action(action_id, from_status, to_status, **fields) -> bool
    # UPDATE ... SET status=?, json=? WHERE action_id=? AND status=?  -> rowcount == 1
def insert_ledger(e: LedgerEntry) -> None
def get_ledger(run_id: str | None = None, limit: int = 500) -> list[LedgerEntry]
def run_costs(run_id) -> dict    # {"credits","inr","calls","cached","errors","by_endpoint":{ep:{"calls","credits"}}}
def cache_get(request_hash) -> dict | None
def cache_put(request_hash, endpoint, response: dict) -> None
def outbox_insert(idempotency_key, channel, to_addr, subject, body) -> str | None
def outbox_get(idempotency_key) -> dict | None
def list_outbox() -> list[dict]
def erp_hold_insert(idempotency_key, cp_id, reason) -> str | None
def erp_hold_get(idempotency_key) -> dict | None
def list_erp_holds() -> list[dict]
def save_watch_event(event_id, watch_id, payload: dict) -> bool
```

For `transition_action`, merge `**fields` into the stored JSON and set the new status in both the column and the JSON. That compare-and-set is what keeps execution exactly-once. Prachi's executor relies on it.

### 4.6 Schema
```sql
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, request_json TEXT, status TEXT,
  started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS counterparties(cp_id TEXT PRIMARY KEY, json TEXT, record_hash TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS steps(run_id TEXT, cp_id TEXT, step TEXT, status TEXT, output_json TEXT,
  updated_at TEXT, PRIMARY KEY(run_id, cp_id, step));
CREATE TABLE IF NOT EXISTS evidence(evidence_id TEXT PRIMARY KEY, run_id TEXT, cp_id TEXT, signal TEXT, json TEXT);
CREATE INDEX IF NOT EXISTS ev_run_cp ON evidence(run_id, cp_id);
CREATE TABLE IF NOT EXISTS assessments(run_id TEXT, cp_id TEXT, rating TEXT, score INT, json TEXT,
  created_at TEXT, PRIMARY KEY(run_id, cp_id));
CREATE TABLE IF NOT EXISTS actions(action_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, run_id TEXT,
  cp_id TEXT, type TEXT, status TEXT, json TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS ledger(call_id TEXT PRIMARY KEY, run_id TEXT, provider TEXT, endpoint TEXT,
  request_hash TEXT, credits REAL, inr REAL, tokens_in INT, tokens_out INT, latency_ms INT,
  status TEXT, note TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS raw_cache(request_hash TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS outbox(outbox_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, channel TEXT,
  to_addr TEXT, subject TEXT, body TEXT, sent_at TEXT);
CREATE TABLE IF NOT EXISTS erp_holds(hold_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, cp_id TEXT,
  reason TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS watch_events(event_id TEXT PRIMARY KEY, watch_id TEXT, payload_json TEXT,
  received_at TEXT, handled INT DEFAULT 0);
```

### 4.7 `config.yaml` and `crd/config.py`

```yaml
version: 1
as_of: null
staleness_days: 45
limits: {max_credits_per_run: 400, max_inr_llm_per_run: 50, max_minutes: 10, max_credits_per_cp: 15}
inr_per_credit: 0.0
prices: {identify: 0.2, enrich: 2.0, job_search: 0.0, web_search: 0.0, web_fetch: 0.0, person_enrich: 0.0,
         watch_notification: 5.0, llm_inr_per_1k_in: 0.0, llm_inr_per_1k_out: 0.0}
identity: {min_confidence: 0.8}
scoring:
  start: 70
  green_min: 65
  amber_min: 40
  positive_cap: 15
  positive_points: {recent_raise: 5, headcount_growth: 5, big_contract: 5}
  coverage_min_signals: 3
  coverage_required_any: [headcount_qoq, headcount_6m, hiring, news]
  points:
    headcount_qoq: [[-20, -20], [-10, -10]]
    headcount_6m:  [[-30, -15], [-15, -8]]
    role_mix: -5
    hiring_stopped: -5
    hiring_distress_roles: -10
    leadership_exit: -10
    funding_runway_months: 18
    funding_runway: -10
    employee_reviews_min_rating: 3.0
    employee_reviews_min_count: 20
    employee_reviews_low: -5
    web_traffic_drop_pct: -30
    web_traffic_drop: -5
    payment_dso_over_terms_days: 15
    payment_behaviour: -10
  news_severity: {hard_stop: hard_stop, fraud_or_raid: -25, layoffs_or_shutdown: -20, operations_halt: -15,
                  lost_client_or_downgrade: -15, leadership_exit: -10, minor: -5, irrelevant: 0}
  hard_stops: [struck_off, liquidation, gst_cancelled, cirp_admitted]
risk_probability: {GREEN: 0.05, AMBER: 0.3, RED: 0.7, CANNOT_VERIFY: 0.5, HELD: 0.5}
actions:
  customer_terms_email: {min_exposure_inr: 1000000, on: [AMBER, RED]}
  credit_hold: {on: [RED]}
  backup_rfq: {on: [RED, AMBER], only_criticality: [sole_source, primary]}
  internal_alert: {on_change: true}
  raise_watch_frequency: {on: [AMBER, RED], every_hours: 24}
report:
  fields: [display_name, role, rating, score, inr_at_risk, top_reason, sources, action_status]
```

`crd/config.py`: `get() -> Config`. `Config` is a pydantic model with `model_config = {"extra": "allow"}` and attribute access to every section; nested sections can be plain dicts. `get()` re-reads the file on every call, which is cheap and means a live config edit applies on the next run. Add `review_id(as_of) -> "2026W47"` (ISO year + week) here too.

**At 13:45:** commit as `p1: contracts, schema, config, skeleton stubs`, push to `main`, and post "contracts pushed" in the chat.

---

## 5. Input adapter: `crd/inputs.py` (13:45–14:00)

```python
def load_counterparties(path: str) -> tuple[list[Counterparty], list[str]]:
    """Read CSV / JSON / XLSX from the kickoff kit. Returns (rows, row_errors)."""
```

- Map the kit's column names onto `Counterparty` with an `ALIASES` dict, e.g. `{"company": "name", "website": "domain", "gst": "gstin", "outstanding": "exposure_inr", "type": "role"}`. Lower-case and strip the headers first.
- Role normalisation: transporter/trucking/fleet → carrier; warehouse → warehouse_lessor; client/shipper → customer; anything else → vendor.
- Domains: strip `http(s)://`, `www.` and paths, and lower-case.
- Money: strip `₹`, commas and spaces. `"42 L"` → 4,200,000; `"1.2 Cr"` → 12,000,000.
- Validate each row separately. On error, append `f"row {i}: {err}"` and continue.
- `record_hash = sha256(json.dumps(row_normalised, sort_keys=True))`.
- Call `db.upsert_counterparty`; it returns True when a record is new or changed. Organizers may change client records mid-build; a changed hash means it must be reviewed this run.

---

## 6. `crd/resolve.py` (13:50–14:15)

```python
def resolve_all(cps: list[Counterparty], client, run_id: str, cfg) -> list[tuple[ResolvedEntity, list[Counterparty], list[Evidence]]]
def resolve_one(cp: Counterparty, client, run_id: str, cfg) -> tuple[ResolvedEntity, list[Evidence]]
```

**`resolve_one` order:**

1. **Domain:** `client.identify(domains=[cp.domain])`. If the top match has `confidence_score >= 0.99`, it is resolved with `match_type="domain"`.
2. **CIN/GSTIN:** if Nilay's `registries.lookup_by_id(cin|gstin)` returns a legal name, call `client.identify(names=[legal_name])` and accept a match whose name normalises to the same string. `match_type="cin"` or `"gstin"`.
3. **Name:** `client.identify(names=[cp.legal_name or cp.name])`. Keep the candidates with confidence ≥ `cfg.identity.min_confidence`.
    - Exactly 1 candidate and (no city/state on the record, or the candidate's location contains the city/state) → resolved, `match_type="name"`.
    - 0 candidates → `not_found`.
    - 2 or more → `ambiguous`. Store up to 5 candidates as `{name, domain, city, confidence, crustdata_company_id}`.
4. If identify raises `SourceUnavailable`, return `status="not_found"` with an `identity` Evidence item at `status="unavailable"`. Scoring turns that into HELD, not CANNOT_VERIFY.
5. Always emit one `identity` Evidence item: claim "Resolved to <legal_name> by domain match (confidence 1.0)", `field_path="matches[0].confidence_score"`, `source="POST /company/identify"`, and `raw_ref=client.last_raw_ref`.

**Name normalisation (`norm()`):** lower-case; strip punctuation; remove `private limited`, `pvt ltd`, `pvt. ltd.`, `limited`, `ltd`, `llp`, `inc`, `india`; collapse whitespace.

**Dedupe in `resolve_all`:**

- Group by, in order: the same `crustdata_company_id`; the same CIN; the same PAN (`gstin[2:12]`); the same normalised domain.
- Within each group, the primary cp is the first by `cp_id`. The entity's `cp_ids` lists all of them; `exposure_inr` is summed; criticality takes the strongest.
- Ambiguous and not-found entities are never merged by name alone.

`tests/test_resolve.py`: a dedupe by GSTIN-PAN, a dedupe by domain, an ambiguous name stays separate, and `norm()` cases.

---

## 7. `crd/scoring.py` (14:00–14:20, refine until 17:15)

A pure function, with no DB, network or LLM:

```python
def score(cps: list[Counterparty], entity: ResolvedEntity, evidence: list[Evidence], cfg, run_id: str,
          previous: str | None = None) -> Assessment
```

**Algorithm:**

```
primary = cps[0]
ev_ok   = [e for e in evidence if e.status == "ok" and not e.stale]
ev_bad  = [e for e in evidence if e.status == "unavailable"]

1. Identity gate
   entity.status == "ambiguous" -> CANNOT_VERIFY
       cannot_verify += "CANNOT VERIFY: which company is '<name>'? Missing: a unique match
                         (domain, CIN or GSTIN). Tried: Company Identify (<n> candidates >= 0.8)."
   entity.status == "not_found" and the identity evidence is "unavailable" -> HELD
   entity.status == "not_found" -> CANNOT_VERIFY ("Missing: any record of this company")

2. Hard stops (stop scoring and return RED)
   legal_status value in {struck_off, liquidation, gst_cancelled} -> hard_stop = value
   insolvency value == "cirp_admitted" (or similar)               -> hard_stop
   news with value.category == "hard_stop"                         -> hard_stop = "insolvency_news"

3. Points (start at cfg.scoring.start)
   headcount_qoq: value (%), thresholds checked in order, first match wins
   headcount_6m:  same
   role_mix: customers only, if value["flag"] is True
   hiring: value {"recent", "prior", "distress_roles"}
       distress_roles non-empty -> hiring_distress_roles
       prior >= 3 and recent == 0 -> hiring_stopped
   leadership_exit: each event (max 2)
   funding_runway: months_since_raise > funding_runway_months AND a headcount signal negative
   news: per relevant article, points = news_severity[category]; take the worst 2, sum them, floor -30
   employee_reviews: rating < min and count >= min_count
   web_traffic: customers only, pct <= web_traffic_drop_pct
   payment_behaviour: dso_days - credit_terms_days > payment_dso_over_terms_days
   positive: sum the positive_points, capped at positive_cap
   score = clamp(start + sum, 0, 100)

4. False-alarm rule
   a news item with category "irrelevant" whose value.subtype is "acquisition|merger|demerger",
   plus a negative headcount -> remove the headcount points and add to conflicts:
   "Headcount drop coincides with <event> (<ev_id>); not treated as distress."

5. Rating
   score >= green_min -> GREEN ; >= amber_min -> AMBER ; else RED

6. Coverage gate (prevents a wrong GREEN)
   distinct_ok_signals = {e.signal for e in ev_ok}
   if rating == GREEN and (len(distinct_ok_signals) < coverage_min_signals
                           or not (distinct_ok_signals & coverage_required_any)):
       rating = CANNOT_VERIFY
       cannot_verify += "CANNOT VERIFY: is <name> healthy? Missing: <the absent signals>. Tried: <sources>."

7. Unavailable rule
   if rating == GREEN and any(e.signal in coverage_required_any for e in ev_bad) -> HELD
   (Also: if rating is AMBER and a hard-stop source, legal_status or insolvency,
    is unavailable, add a cannot_verify line naming it.)

8. Exposure
   inr_at_risk = sum(cp.exposure_inr for cp in cps) * risk_probability[rating]

9. change_since_last = f"{previous} -> {rating}" if previous and previous != rating else None
```

Every `SignalScore` carries the evidence ids it used, and a `note` such as "qoq −22% ≤ −20% → −20".

**`tests/test_scoring.py`:** write these 12 tests; each builds Evidence by hand.

1. Healthy with 4 signals → GREEN.
2. Healthy but only identity + reviews → CANNOT_VERIFY, and the "Missing:" line names headcount/hiring/news.
3. qoq −25% → −20 points.
4. CIRP → RED with a hard stop, whatever the other points.
5. Struck off → RED.
6. Ambiguous identity → CANNOT_VERIFY with the candidate count.
7. GREEN but news unavailable → HELD.
8. Acquisition plus a headcount drop → points neutralised, a conflict listed.
9. Positives capped at +15.
10. Stale evidence → ignored.
11. A duplicate entity sums exposure.
12. Determinism: same inputs in a different evidence order → identical output.

---

## 8. `crd/orchestrator.py` (14:30–15:30)

```python
STEPS = ["resolve", "gather", "score", "propose"]

def run_review(raw_text: str, input_path: str | None = None, run_id: str | None = None) -> str
def run_review_async(raw_text: str, input_path: str | None = None) -> str
def run_single(domain_or_cp_id: str, trigger: str = "watcher") -> str
def resume() -> str | None
def run_progress(run_id: str) -> dict
```

**`run_review` flow:**

```
cfg = config.get()
if input_path: cps, row_errors = inputs.load_counterparties(input_path)
else: cps = db.list_counterparties()
req = request_parser.parse(raw_text, cps, cfg)            # scope, ids, as_of
if req.clarification: return a run with status "needs_clarification" (Prachi shows the question)
run_id = run_id or db.new_run(req)
client = CrustdataClient(run_id)
selected = filter cps by req.scope / req.cp_ids
resolved = step-cached resolve_all(...)        # step "resolve", keyed cp_id="__all__"
deadline = start + cfg.limits.max_minutes
for entity, group, id_ev in resolved:
    cid = group[0].cp_id
    try:
        ev   = cached("gather",  lambda: id_ev + signals.gather(group[0], entity, client, cfg))
        a    = cached("score",   lambda: scoring.score(group, entity, ev, cfg, run_id, db.previous_rating(cid, run_id)))
        a.explanation = explain.write(a, ev, run_id)     # inside the score step, before mark done
        acts = cached("propose", lambda: policy.propose(a, group[0], cfg, config.review_id(req.as_of)))
    except (BudgetExceeded, CreditsExhausted) as e:
        db.finish_run(run_id, "partial"); note the unreviewed cps; break
    if time.time() > deadline: finish "partial"; break
executor.execute_approved(run_id)          # runs the reversible auto actions
render.render(run_id)
db.finish_run(run_id, "done")
```

**`cached(step, fn)` helper:** if `step_status == "done"`, rebuild the result from `get_step_output` and return it. Otherwise:

1. `mark_step(running)`;
2. call `fn()`;
3. persist the result:
    - gather → `save_evidence` and output `{"evidence_ids": [...]}`;
    - score → `save_assessment` and output `a.model_dump(mode="json")`;
    - propose → output `{"action_ids": [...]}`;
4. `mark_step(done, output)`.

Make writes idempotent: evidence uses INSERT OR REPLACE, actions use INSERT OR IGNORE.

**`resume()`:** `executor.reconcile()` → `rid = db.last_unfinished_run()` → `run_review(req.raw_text, None, run_id=rid)`, reading the request from the run row. Steps already `done` are skipped.

**`run_review_async`:** create the run row first, so the run_id is known, then `threading.Thread(target=run_review, kwargs={..., "run_id": rid}, daemon=True).start()`. Return `rid`.

**`run_single(domain_or_cp_id)`:** find the counterparty by `cp_id` or domain, then `run_review(f"Re-check {cp.name}", None)` with `scope="ids"`. Pass trigger="watcher" through.

**`run_progress`:**

```python
{"run_id": ..., "status": ..., "total": n_entities, "done": n_with_propose_done,
 "steps": db.list_steps(run_id), "costs": db.run_costs(run_id),
 "recent_calls": [l.model_dump(mode="json") for l in db.get_ledger(run_id, limit=10)]}
```

**Kill tests (do them; they are the stage demo):**

1. `CRD_MOCK=0`, 10 counterparties. `kill -9` during gather, then `python -m crd.cli resume`. Afterwards check that there are no duplicate evidence ids, that resolved counterparties are not re-fetched (cached ledger rows are fine), and that the final report matches an uninterrupted run.
2. Approve an action with `CRD_SLOW_EXECUTE=5`, then `kill -9` during the sleep and restart → exactly one `outbox` row. (Prachi owns the executor; you run the test together.)

---

## 9. `crd/request_parser.py` (15:00–15:20)

```python
def parse(text: str, cps: list[Counterparty], cfg) -> ReviewRequest
```

1. **Rules first** (deterministic and free):
    - "all", "everyone" or "this week's review" → scope all;
    - "carrier", "supplier", "vendor", "transporter" → suppliers;
    - "customer", "client", "receivable" → customers;
    - any `cp_id` or any `norm(name)` from the list found in `norm(text)` → scope ids.
2. If the rules find nothing, call `llm.complete_json(system, text, ReviewRequest-like schema)` to extract the names. Map them back to cps with `norm()`.
3. A name is mentioned but matches no cp, or matches two → set `clarification = "I couldn't match 'X' to one counterparty in your list. Did you mean A or B?"`. Never guess.
4. `as_of` = `cfg.as_of` or today. Phrases like "as of 1 Nov" can be parsed by rules.
5. Same meaning, different words → same `ReviewRequest`. Add 3 reworded variants to the determinism test.

## 10. `crd/explain.py` (15:20–15:45)

```python
def write(a: Assessment, ev: list[Evidence], run_id: str) -> str
def why(run_id: str, cp_id: str) -> dict
```

- `write`:
    1. Build a facts list from the top 3 SignalScores by absolute points: claim, record_date, evidence_id.
    2. Ask `llm.complete_json` for `{"text": str}`, with the instruction: 2 sentences, plain English, only these facts, and each fact followed by `[ev_xxx]`.
    3. **Check:** every number and date in the text must appear in the facts' claims or dates, and every `[ev_…]` must be from the list.
    4. If the check fails or the LLM is unavailable, use a template: `"{RATING}: {claim1} [ev1]; {claim2} [ev2]."` For CANNOT_VERIFY, use the first `cannot_verify` line.
- `why`: returns `{"start": 70, "signals": [{"signal","points","note","evidence":[{"id","claim","url","field_path","record_date"}]}], "hard_stop", "coverage": {"ok_signals": [...], "required": [...], "passed": bool}, "final_score", "rating", "unavailable": [...], "conflicts": [...]}`. Prachi renders this on the detail page; the CLI prints it as a table.

## 11. `crd/cli.py` and `tests/run_cases.py` (15:30–16:30)

These Typer commands are the screening interface, so keep them stable:

```
init
review TEXT [--input PATH] [--mock/--live]
resume
status [RUN_ID]
why CP_ID [--run RUN_ID]
approve ACTION_ID --by NAME
reject ACTION_ID --by NAME
execute
test [--mock/--live] [--case GLOB]
costs [RUN_ID]
```

- `review` prints the run id, a summary table (cp, rating, score, ₹ at risk, top reason) and the output paths.
- `approve` sets `approved_by`, `approved_at` and `draft_hash = sha256(draft)`, then calls `transition_action(pending_approval → approved)`. This gives a CLI fallback if the UI breaks on stage.

**`tests/run_cases.py`** (Prachi writes the yaml cases; you write the runner):

```
for each tests/cases/*.yaml:
  fresh temp DB (set CRD_DB_PATH to a tmp file)
  write input_rows to a temp CSV -> run_review(case.request, csv)
  assessments = db.get_assessments(run_id)
  check: rating in expected.rating (str or list); hard_stop if given; merged_cp_ids if given;
         every must_cite substring found in some evidence (url, source or field_path) of that cp;
         no must_not rating
  record: PASS/FAIL, reason, credits, seconds
print a table, write var/test_report.json, exit 1 if any FAIL
```

A wrong GREEN is a **hard fail**; print it in red. Run the full suite at 16:30 and again at 17:40.

---

## 12. Integration and git (you are the merger)

- Your branch is `p1-core`. Merge to `main` at 13:45, 14:20, 15:30, 16:30, 17:15 and 17:45.
- Merge order at each sync:
    1. Pull all three branches.
    2. Merge `p2-data`, run `python -m pytest -q`.
    3. Merge `p3-product`, run `python -c "import crd.web.app"`.
    4. `python -m crd.cli review "Run this week's review" --input data/input/counterparties.csv --mock`.
    5. Push `main`, then tell both teammates to rebase.
- A contract change request comes in → decide within 10 minutes. Prefer **adding** an optional field over renaming one. Announce every change in the chat.
- Never squash history; the judges read the commits.
- **17:15 freeze:** after this, only bug fixes go to `main`, and only you merge.
- **17:45:** `git tag v1 && git push --tags`. Check that the repo is public and its first commit is after 10:15.

## 13. Demo and finals: your parts

- **Kill on stage:** you run the CLI or the server. Have the commands in a script, `scripts/demo_kill.sh`: start a run → wait 8 s → `kill -9` → `resume` → print `/outbox` rows.
- **Live fix (5 min):** most likely asks and where to change them:
    - New output field → `config.report.fields`, plus put the value in `Assessment.extra` from scoring.
    - Changed threshold → `config.yaml`.
    - New news category → `news_severity`, plus Nilay's classifier enum.
    - Lower cost cap → `limits`.
    - New hard stop → `hard_stops`, plus a scoring branch.

  Practise one of each before 17:15.
- **System-design question** (100× volume or a second client):
    - A job queue (one job per counterparty-step) with workers.
    - A shared cache keyed on company id across clients.
    - Watchers instead of polling.
    - Per-client config and ledger (a `client_id` column).
    - Postgres instead of SQLite, with rate-limit buckets shared through Redis.
    - Idempotency keys already make the workers safe to retry.

## 13a. Things your teammates' files expect from you (add these early)

| From | They need | Where |
|---|---|---|
| Nilay | A `llm: {model: "...", max_tokens: 800}` key in `config.yaml` | config |
| Nilay | An IBBI `none_found` insolvency evidence item **must not count** toward the coverage gate. Add `coverage_exclude: [{signal: insolvency, value: none_found}]` and honour it in scoring step 6 | config + scoring |
| Nilay | Call `crd.data.people.purge_people_raw(run_id)` inside the run's finish path | orchestrator |
| Nilay | `registries.lookup_by_id(cin, gstin, client)` exists; use it in resolve step 2 | resolve |
| Nilay + Prachi | An env flag `CRD_FAIL_SOURCES=registries,news` that Nilay's client honours (it raises SourceUnavailable for those sources), used by test case 10 | `.env.example` |
| Prachi | `client_name`, `webhook_url`, and `actions.customer_terms_email.new_terms: {RED: 15, AMBER: half}` keys in `config.yaml` | config |
| Prachi | A run status `needs_clarification`, with the question stored in the run's request JSON | orchestrator + db |
| Prachi | `db.get_run`, `db.list_runs`, `db.outbox_get`, `db.erp_hold_get`, `db.list_outbox`, `db.list_erp_holds`, `db.get_evidence_by_ids` (all listed in 4.5) | db |

## 14. Definition of done

- [ ] Contracts on `main` by 13:45; never broken afterwards.
- [ ] `review` runs the full list in live mode, inside the credit and time caps.
- [ ] `kill -9` at any point, then `resume`, gives the same final result with no duplicate actions.
- [ ] `test` passes ≥ 90% of the cases, with **zero wrong GREENs**.
- [ ] A reworded request gives an identical rating and score.
- [ ] `why` explains every point of every score.
- [ ] You have practised a config-only live fix.

## 15. Kickoff prompt for your Claude session

> Read CLAUDE.md, docs/TEAM_PLAN.md and docs/ARTH_core.md. I am Arth (P1 Core, the integrator). Do section 4 now: create the repo skeleton with stubs for every file in TEAM_PLAN section 3.1 (raise NotImplementedError in other people's stubs, with the exact signatures from my doc), then write crd/models.py, crd/db.py (every function in 4.5 with the schema in 4.6), crd/config.py, config.yaml, requirements.txt, .env.example and .gitignore exactly as specified. Make `python -c "import crd.db; crd.db.init_db()"` work. Stop after that so I can push. Next tasks will be sections 5–11, one at a time.
