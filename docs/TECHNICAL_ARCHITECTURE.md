# Trust Issues: technical architecture

**T**hreat and **R**isk **U**nderstanding **S**ystem for **T**racking **I**nstitutional **S**tability.
This document is the engineering view: what the system is made of, how a review flows through it,
and which mechanism enforces each trust guarantee. The business view is in
[`BUSINESS_ARCHITECTURE.md`](BUSINESS_ARCHITECTURE.md); the step-by-step run notes are in
[`architecture.md`](architecture.md).

All diagrams are Mermaid and render on GitHub.

---

## 1. System context

Who and what Trust Issues talks to.

```mermaid
flowchart LR
  subgraph Client["3PL (our customer)"]
    CA["Credit / ops analyst<br/>asks for reviews, reads verdicts"]
    AP["Named approver<br/>(senior for strategic accounts)"]
    LIST[("Counterparty list<br/>CSV · JSON · XLSX")]
  end

  TI["<b>Trust Issues</b><br/>counterparty risk desk"]

  subgraph Data["Evidence sources"]
    CD["Crustdata API<br/>Identify · Enrich · Job Search ·<br/>Person Enrich · Web Search/Fetch · Watcher"]
    IBBI["IBBI public announcements<br/>(insolvency hard stops)"]
  end
  LLM["Claude (Anthropic)<br/>extraction + summaries only"]

  subgraph Out["Where actions land"]
    INBOX["Team inbox (email)"]
    SLACK["Slack channel"]
    ERP["ERP credit hold (mock)"]
  end

  CA -- "plain-English request + list" --> TI
  LIST --> TI
  TI -- "verdicts, evidence, drafts" --> CA
  TI -- "approval queue" --> AP
  AP -- "approve / reject / edit" --> TI
  TI <-- "rate-limited, cached, costed calls" --> CD
  TI <-- "public listing, no login" --> IBBI
  TI <-- "fetched text in, structured facts out" --> LLM
  CD -- "watch webhook: signals moved" --> TI
  TI -- "exactly once, after approval" --> INBOX & SLACK & ERP
```

## 2. Components

Every external call goes through one client; every write goes through `crd/db.py`.

```mermaid
flowchart TB
  subgraph Entry["Entry points"]
    WEB["Web app<br/>FastAPI + Jinja2 + HTMX<br/><code>crd/web/app.py</code>"]
    CLI["CLI<br/><code>crd/cli.py</code>"]
    HOOK["Webhook<br/><code>/webhooks/crustdata</code>"]
  end

  subgraph Core["Core pipeline (P1)"]
    RP["Request parser<br/><code>request_parser.py</code>"]
    IN["Input adapter<br/><code>inputs.py</code>"]
    ORC["Orchestrator<br/>checkpoints · resume · caps<br/><code>orchestrator.py</code>"]
    RES["Resolve + dedupe<br/><code>resolve.py</code>"]
    SC["Scoring + coverage gate<br/>pure function of config<br/><code>scoring.py</code>"]
    EX["Explain<br/>LLM text checked vs facts<br/><code>explain.py</code>"]
  end

  subgraph DataL["Data layer (P2)"]
    SIG["Signals<br/><code>data/signals.py</code>"]
    NEWS["News + namesake filter<br/><code>data/news.py</code>"]
    REG["Registries<br/><code>data/registries.py</code>"]
    PPL["People (public roles only)<br/><code>data/people.py</code>"]
    CDC["Crustdata client<br/>token bucket · retries · cache ·<br/>raw store · ledger row<br/><code>data/crustdata.py</code>"]
    LLMC["LLM wrapper<br/><code>llm.py</code>"]
  end

  subgraph Act["Actions + product (P3)"]
    PRI["Priority<br/>rating × importance<br/><code>actions/priority.py</code>"]
    POL["Policy<br/>what to propose<br/><code>actions/policy.py</code>"]
    EXE["Executor<br/>compare-and-set · reconcile<br/><code>actions/executor.py</code>"]
    CH["Channels<br/><code>actions/channels.py</code>"]
    REPT["Report<br/>json · md · html · csv<br/><code>report/render.py</code>"]
  end

  CFG[["config.yaml<br/>thresholds · points · actions · priority · report fields"]]
  DB[("SQLite via <code>crd/db.py</code><br/>runs · steps · evidence · assessments ·<br/>actions · outbox · erp_holds · ledger · raw_cache")]

  WEB & CLI --> RP --> ORC
  WEB & CLI --> IN --> DB
  HOOK --> ORC
  ORC --> RES --> CDC
  ORC --> SIG --> CDC
  SIG --> NEWS & REG & PPL
  NEWS --> LLMC
  NEWS & REG & PPL --> CDC
  ORC --> SC --> EX --> LLMC
  ORC --> POL --> PRI
  POL --> DB
  WEB -- "approve" --> EXE --> CH
  ORC --> EXE
  ORC --> REPT
  CFG -.-> SC & POL & PRI & REPT
  CDC & LLMC -- "ledger row per call" --> DB
  ORC & RES & SC & EXE & CH -.-> DB
```

## 3. One review, end to end

```mermaid
sequenceDiagram
  autonumber
  actor U as Analyst
  participant W as Web / CLI
  participant O as Orchestrator
  participant R as Resolve
  participant C as Crustdata client
  participant S as Scoring
  participant P as Policy + priority
  participant D as SQLite
  actor A as Approver
  participant E as Executor
  participant X as Channels

  U->>W: "Run this week's review on our carriers"
  W->>O: parse request → scope (rules first; unknown name → clarifying question)
  O->>D: new run, status running
  O->>R: resolve all rows (batched Identify, 5/min key)
  R->>C: identify(domains), then identify(names)
  C-->>R: matches (cached, costed, raw stored)
  R-->>O: entities · duplicates merged · lookalikes = ambiguous
  O->>D: checkpoint resolve
  loop each entity
    O->>C: gather (Enrich, Job Search, news, registries)
    C-->>O: Evidence rows (failures = unavailable)
    O->>D: checkpoint gather
    O->>S: score(evidence, config)
    S-->>O: verdict, score, ₹ at risk, why
    O->>D: checkpoint score
    O->>P: propose(actions, priority)
    P->>D: insert actions (idempotency key: cp:type:week)
  end
  O->>D: finish run · render report
  A->>W: approve draft (name, maybe edit; senior for strategic)
  W->>D: pending_approval → approved (hash of approved text)
  E->>D: approved → executing (compare-and-set)
  E->>X: write channel record keyed by idempotency key
  X-->>E: external ref
  E->>D: executing → done
```

A `kill -9` at any point is safe: `resume()` reconciles actions left in `executing`, then continues
the unfinished run from its last checkpoint without repeating paid calls.

## 4. The action lifecycle

```mermaid
stateDiagram-v2
  [*] --> approved: reversible (internal alert, daily watch, account call)
  [*] --> pending_approval: irreversible (terms email, credit hold, backup RFQ)
  [*] --> proposed: guarantee request (draft only, never sent)
  pending_approval --> approved: named approver<br/>(+ senior if strategic)
  pending_approval --> rejected: approver says no
  approved --> executing: compare-and-set
  executing --> done: channel record written
  executing --> approved: reconcile() after a crash,<br/>no channel record found
  executing --> done: reconcile() after a crash,<br/>channel record found
  approved --> pending_approval: draft text changed after approval<br/>(hash mismatch)
  executing --> failed: channel error
  failed --> approved: retry
  done --> [*]
  rejected --> [*]
```

## 5. Data model

```mermaid
erDiagram
  RUNS ||--o{ STEPS : checkpoints
  RUNS ||--o{ EVIDENCE : gathers
  RUNS ||--o{ ASSESSMENTS : produces
  RUNS ||--o{ ACTIONS : proposes
  RUNS ||--o{ LEDGER : costs
  COUNTERPARTIES ||--o{ ASSESSMENTS : "rated in"
  COUNTERPARTIES ||--o{ EVIDENCE : "about"
  ACTIONS ||--o| OUTBOX : "sent once as"
  ACTIONS ||--o| ERP_HOLDS : "applied once as"
  LEDGER }o--o| RAW_CACHE : "served from"

  RUNS { text run_id PK  text request_json  text status  text note }
  COUNTERPARTIES { text cp_id PK  text json  text record_hash }
  STEPS { text run_id  text cp_id  text step  text status  text output_json }
  EVIDENCE { text evidence_id PK  text run_id  text cp_id  text signal  text json }
  ASSESSMENTS { text run_id  text cp_id  text rating  int score  text json }
  ACTIONS { text action_id PK  text idempotency_key UK  text run_id  text cp_id  text type  text status }
  OUTBOX { text outbox_id PK  text idempotency_key UK  text channel  text to_addr }
  ERP_HOLDS { text hold_id PK  text idempotency_key UK  text cp_id }
  LEDGER { text call_id PK  text run_id  text provider  text endpoint  real credits  real inr  int tokens_in  int tokens_out  text status }
  RAW_CACHE { text request_hash PK  text endpoint  text response_json }
```

Every `Evidence` row carries `source`, `field_path` or `passage`, `url` or `raw_ref`, `record_date`
and `retrieved_at`. A fact without them is not stored.

## 6. Trust guarantees and what enforces them

| Guarantee | Mechanism | Proven by |
|---|---|---|
| No fact without a source | `Evidence` requires source + path/passage + url/raw_ref + dates; UI links each to its raw answer (**Sources** tab) | `tests/test_signals.py`, `tests/test_e2e_fixtures.py` |
| Silence is not health | Failed or timed-out source → `status="unavailable"`; a GREEN that relied on it → **HELD**; coverage gate blocks GREEN on thin evidence | `test_scoring.py::test_green_with_news_unavailable_is_held`, case 10 |
| Never guess identity | Unique only if top match = 1.0 and the next < 0.9; lookalikes → **CANNOT VERIFY**; dedupe never by name alone | `tests/test_resolve.py`, cases 04, 05, 06 |
| Deterministic verdicts | Scoring is a pure function of evidence + `config.yaml`; LLM never rates | case 11 (reworded), `test_deterministic_order` |
| Nothing irreversible without a human | Policy marks irreversible actions `pending_approval`; approve route requires a name, senior for strategic | `tests/test_executor.py`, `tests/test_priority.py` |
| Exactly once | Idempotency key `{cp}:{type}:{week}` unique in actions, outbox, erp_holds; compare-and-set; `reconcile()` on startup | `test_executor.py` (crash before/after write, 4 racing threads), kill demo |
| Kill-safe runs | Step checkpoints in SQLite; `resume()` skips finished steps, reuses cached calls | `test_orchestrator.py::test_kill_and_resume` |
| Bounded cost | Token buckets per endpoint; credit and time caps stop a run as `partial`; cache makes a re-run ~0 credits | `tests/test_crustdata_client.py`, ledger |
| Personal data minimum | People: name, title, employer, dates only; person raw data purged after each run; Sources tab never shows person endpoints and withholds contact fields | `tests/test_people.py` |
| Fetched text is data | LLM extracts into a schema; nothing in fetched text can trigger an action | `tests/test_news.py`, `tests/test_llm.py` |

## 7. Runtime and deployment

```mermaid
flowchart LR
  subgraph Local["Local / server (full product)"]
    UV["uvicorn crd.web.app:app"] --> SQL[("SQLite file<br/>var/crd.db")]
    UV --> FIX[("tests/fixtures<br/>(mock mode)")]
    UV -->|live mode| API["Crustdata + Anthropic"]
  end
  subgraph Vercel["Vercel (hosted read-only demo)"]
    FN["Python function<br/>api/index.py → crd.web.app"] --> TMP[("/tmp/crd.db<br/>copied from the bundled demo DB<br/>at cold start")]
    FN --> FIX2[("bundled fixtures")]
  end
```

- **Modes:** `CRD_MOCK=1` replays recorded Crustdata answers from `tests/fixtures/` at 0 credits;
  a company with no recording is unavailable, never invented. Live mode needs
  `CRUSTDATA_API_KEY` (and optionally `ANTHROPIC_API_KEY`) in `.env`.
- **Hosted demo (Vercel):** serverless functions have a read-only filesystem, no long-running
  background work and short time limits, so the hosted copy is a **read-only demo**: it serves the
  bundled fictional portfolio, lets you browse, filter, simulate and open every file, and turns
  "Run review" off. Approvals made there live only in that instance's `/tmp`. Real reviews run on a
  machine with a persistent disk (`uvicorn`), which is also where the kill-and-resume guarantees hold.
- **Stack:** Python 3.11+, FastAPI, Jinja2, HTMX, SQLite, httpx, pydantic v2. No front-end build
  step; the UI is server-rendered with a little vanilla JS (`crd/web/static/crd.js`, `front.js`).
