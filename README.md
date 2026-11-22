# Trust Issues

**An agent that runs the weekly counterparty risk review for a 3PL.** It gives every carrier,
warehouse and customer a sourced rating, proposes protective actions that a person approves, and
executes each one exactly once.

ContextCon 05 · BITS Pilani · 22 Nov 2026 · Theme: Supply chain + Trust and compliance
Team: Arth (P1 Core) · Nilay (P2 Data) · Prachi (P3 Product)

Architecture: [`docs/architecture.md`](docs/architecture.md) · Demo video: _link on the submission form_

---

## 1. The service

A third-party logistics company (3PL) lives on two kinds of counterparties:

- **suppliers** (carriers, warehouse lessors, vendors): if one fails, shipments stop;
- **customers** on credit terms: if one fails, the invoices are lost.

**Who does this review today:** credit-control analysts at the 3PL, trade-credit insurers
(who price the cover from it), rating agencies, and supplier-risk monitoring firms. It is slow,
manual and usually monthly or quarterly. **Who pays:** the 3PL's CFO or head of credit, out of
the credit-control and insurance budget.

**What we replace:** the analyst's weekly loop. Look up each counterparty, read the signals,
decide a rating, decide what to do, draft the emails, chase approvals, and keep the record.

## 2. Input and finished result

**Input**
- **A counterparty list** (CSV, JSON or XLSX) with `cp_id`, `name`, optional `legal_name`,
  `domain`, `cin`, `gstin`, `city`, plus `role` (carrier / warehouse_lessor / vendor / customer),
  `criticality` (sole_source / primary / backup / na), `exposure_inr`, `credit_terms_days`,
  `dso_days` and `account_owner`. Values like "₹42 L" and "1.2 Cr" are accepted.
- **A request in plain words:**
  - "Run this week's risk review on all our carriers and customers";
  - "Check our customer Tide India before we renew 30-day terms";
  - "Is Delhivery still safe as our main line-haul partner?".

**Finished result**
1. **A review** (`var/runs/<run>/review.json`, `.md`, printable `.html`). Each counterparty gets
   GREEN / AMBER / RED / CANNOT VERIFY / HELD, a score, ₹ at risk, a two-sentence explanation and
   the evidence. The review is ranked by **priority** (rating × importance), then ₹ at risk.
2. **An approval queue.** Drafted terms emails, credit holds and backup-carrier RFQs, each with its
   reasons, its sources and a one-line "why this action".
3. **Executed actions** in the outbox, mock ERP and Slack, each exactly once, with its idempotency
   key.
4. **A cost ledger.** Every API and LLM call with its credits, ₹, tokens and latency
   (`ledger.csv`).

## 3. Pass mark

| Check | Target |
| --- | --- |
| Ratings vs test answers | ≥ 90%, **zero wrong GREENs** |
| Hard cases (lookalike, duplicate, no footprint, namesake, source failure) | 100% |
| Facts with a source and a date | 100% |
| Unapproved irreversible actions | 0 |
| Duplicate actions after a kill | 0 |
| Credits per counterparty | under the cap (`limits.max_credits_per_cp: 15`) |

## 4. How to run it (fresh clone)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # add CRUSTDATA_API_KEY / ANTHROPIC_API_KEY for live mode
python -m crd.cli init

# mock mode: no credits, reads the recorded responses in tests/fixtures/
python -m crd.cli review "Run this week's risk review on all our carriers and customers" \
    --input data/input/sample_counterparties.csv --mock

# live mode: real Crustdata and Claude calls, every call in the ledger
python -m crd.cli review "Run this week's risk review on all our carriers and customers" \
    --input data/input/sample_counterparties.csv --live

# web UI: dashboard, live progress, approvals, outbox, ledger
uvicorn crd.web.app:app --port 8000          # open http://localhost:8000

# tests
python -m pytest -q tests                    # unit and integration tests
python -m crd.cli test --mock                # the 12 graded cases in tests/cases/
```

On Windows, set `PYTHONUTF8=1` (several files contain ₹). Kill demo:
`bash scripts/demo_kill_approval.sh` (or `scripts\demo_kill_approval.ps1`) after a review has
created some pending approvals.

## 5. How it's built

See [`docs/architecture.md`](docs/architecture.md) for the diagram and the step-by-step flow.
In one paragraph:

The **orchestrator** runs resolve → gather → score → propose for each entity, and checkpoints
every step in **SQLite**. A killed run resumes where it stopped and never repeats a paid call.
**Crustdata is called only through `crd/data/crustdata.py`**, which provides token-bucket rate
limits (5/min, measured), retries with backoff, a response cache, a raw-response store and a ledger
row per call. **Scoring is a pure function of `config.yaml`**, so a threshold or new field is a
config edit. **Actions** carry an idempotency key `{cp_id}:{type}:{review week}`. The executor
moves `approved → executing → done` by compare-and-set, and channels write their record keyed by
that key first. On every startup, `reconcile()` settles anything a crash left in `executing`. The
**web UI** is FastAPI + Jinja2 + HTMX, with no build step.

## 6. Data sources it calls

| Source | Endpoint | Used for | Cost (measured) |
|---|---|---|---|
| Crustdata Identify | `POST /company/identify` | domain / name → company; lookalike detection | 0.2 credit per match |
| Crustdata Company Enrich | `POST /company/enrich` | headcount (QoQ, 6 months, role mix), hiring, news[], employee reviews, web traffic, funding | 2 credits |
| Crustdata Job Search | `POST /job/search` | hiring freeze and distress-role postings | 0.03 per result returned |
| Crustdata Person Enrich | `POST /person/enrich` | CEO/CFO exits (public role and dates only) | 1 credit |
| Crustdata Web Search / Fetch | `POST /web/search/live`, `/web/enrich/live` | news beyond Enrich; registry pages via `site:` search | 1 credit per page / URL |
| Crustdata Watcher | `POST /watch/company` + our `/webhooks/crustdata` | re-check one counterparty when its signals move | 5 credits per notification |
| IBBI public announcements | ibbi.gov.in/public-announcement | CIRP / liquidation hard stops | free |
| Company status (in place of MCA) | Crustdata company record | struck off / inactive | in Enrich |
| Claude (`claude-sonnet-4-5`) | Anthropic Messages API | **only** extracts facts from fetched text, classifies news (with a namesake check in code), and writes the two-sentence summary, which is checked against the facts | tokens in the ledger |

**Which registries work (checked 22 Nov 2026):**
- **IBBI public announcements work.** The page is a public listing with no login or CAPTCHA. A match
  on the normalised legal name within 2 years gives a `cirp_admitted` or `liquidation` hard stop. 2+
  similar debtors count as ambiguous, never a guess.
- **MCA blocks automated access** (HTTP 403 and a CAPTCHA). Legal status comes from Crustdata's
  company record instead, and the claim says MCA was not checked.
- **GST taxpayer search** sits behind a CAPTCHA and is reported as not checked.

We never bypass a CAPTCHA or login. A registry that errors or times out is `unavailable`, so a
counterparty that would otherwise be GREEN is **held** (HELD) instead of passed on silence.

**What the LLM is not used for:** the rating, the score, the action choice, or any fact without a
source.

## 7. Where a person approves

| Action | Reversible | Who | When |
|---|---|---|---|
| Internal alert (Slack / outbox) | yes | automatic | rating changed, RED, or needs a person |
| Watch daily (Crustdata Watcher) | yes | automatic | AMBER or RED |
| Account-owner call | yes | automatic | AMBER customer who depends on us heavily (instead of a terms cut) |
| **Customer terms email** | **no** | **named approver** | customer AMBER/RED with exposure ≥ ₹10 L (≥ ₹5 L if they can switch easily) |
| **ERP credit hold** | **no** | **named approver** | customer RED |
| **Backup-carrier RFQ** | **no** | **named approver** | sole-source / primary / strategic supplier AMBER/RED |
| Guarantee request | no | draft only, never sent | customer RED with exposure ≥ ₹20 L |

The approval flow:
1. In `/approvals` the person sees the draft as an email preview, the reasons with source links,
   the priority and why the action was proposed.
2. They type their name, may edit the draft, and click **Approve & send once**.
3. **Strategic accounts need a senior approver** (a checkbox the server enforces).
4. The approved text is hashed. If it changes after approval, the executor refuses and the action
   returns to the queue.
5. During the event, every outbound message goes **only to our team inbox**.

## 8. Costs

Measured on the probe run (`tests/fixtures/README.md`), one counterparty costs about:

| Call | Credits |
|---|---|
| Identify (batched) | 0.2 |
| Company Enrich | 2.0 |
| Job Search (small limit) | ~0.3 |
| Web Search (news) | 1.0 |
| Web Fetch (1–2 articles) | 1–2 |
| Person Enrich (only when a CXO changed) | 0–1 |
| **Total per weekly review** | **≈ 4.5–6.5 credits** (cap 15) |

The second review in the same week reuses the cache and costs close to 0 credits. LLM tokens are
about 2–4k per counterparty. The ledger shows the exact ₹ once `inr_per_credit` is set in
`config.yaml`.

**Price:** we would charge **₹1,500 per counterparty per month** (four weekly reviews plus
Watcher re-checks). That is a fraction of an analyst's time or a trade-credit insurer's
monitoring fee. At ₹2–3 per credit, the data cost is about ₹50–80 per counterparty per month,
plus about ₹10–20 of LLM. That leaves a **gross margin above 90%** before hosting and support.

## 9. Ethics

- **Companies only.** People appear only as a company's officers in public roles (name, title,
  employer, dates). No contact enrichment, photos or posts. Person data is purged after each run.
- **No fact without a source.** Every claim links to its URL or stored raw response, with its
  record date. Stale evidence is shown but not scored.
- **Cannot verify instead of punishing small firms.** A firm with no web footprint, or a common name
  we cannot pin down, is CANNOT VERIFY ("Missing: GSTIN or CIN"), never RED.
- **Silence is not health.** A failed source is `unavailable`, and a GREEN that relied on it becomes
  HELD.
- **People decide.** Nothing irreversible runs without a named approver, and strategic accounts need
  a senior one. Messages go only to our own inbox during the event.
- **Explanations.** Every rating has a "why this rating" breakdown (start 70, points per signal,
  hard stop, coverage check), and every action has a "why".
- **Prompt injection.** Fetched web text is data. The LLM extracts fields into a schema, suspicious
  instructions are flagged, and nothing in fetched text can trigger an action.

## 10. How we tested

- **Unit and integration tests** (`python -m pytest -q tests`, **131 tests, all passing**,
  Windows included):
  - scoring rules, resolve and dedupe, the orchestrator (including a simulated `kill -9` and resume),
    the Crustdata client on recorded responses, news, registries, people, the watcher, the LLM
    wrapper, and the end-to-end pipeline on fixtures;
  - **exactly-once** (`tests/test_executor.py`): execute twice → one email; crash after the channel
    write → reconcile → done, still one row; crash before the write → reconcile → sent once; draft
    edited after approval → back to the queue, nothing sent; four threads racing → one row;
    duplicate propose → no second action;
  - **priority** (`tests/test_priority.py`): same rating, different action by importance; senior
    sign-off enforced on the approve route.
- **12 graded cases** (`tests/cases/*.yaml`, `python -m crd.cli test`). Real companies and events
  dated after 1 Sep 2026; sources are in each file's `notes`:

| # | Case | Company / event | Expected |
|---|---|---|---|
| 01 | Healthy carrier | Delhivery: Q2 FY27 profit, 30 Oct 2026 | GREEN, cites headcount |
| 02 | Distressed customer | Tide India: member business wind-down, ~400 staff, 11 Nov 2026 | AMBER/RED |
| 03 | Insolvency | Quasar India Ltd: CIRP admitted by NCLT New Delhi, Oct/Nov 2026 | RED, hard stop, cites IBBI |
| 04 | Lookalike | "Sharma Transport": 5+ Identify matches, one at 1.0 with no domain | CANNOT VERIFY |
| 05 | Duplicate | Blue Dart (brand only) + Blue Dart Express Ltd (legal name + domain) | merged into one |
| 06 | No footprint | "Shree Ganesh Roadlines Bhiwandi" | CANNOT VERIFY |
| 07 | False alarm | India Glycols: demerger effective 1 Sep 2026, staff moved to the new companies | not RED |
| 08 | Leadership exit | Mahindra Logistics: MD & CEO resigned, announced 16 Nov 2026 | AMBER/RED |
| 09 | Namesake news | Tide Water Oil (lubricants), not Tide (fintech) | not RED |
| 10 | Source failure | Delhivery with Enrich forced to fail | HELD / CANNOT VERIFY, never GREEN |
| 11 | Reworded | Case 01 asked as a question | same as 01 |
| 12 | Kill mid-action | RED/AMBER customer over ₹10 L, terms email approved, server killed | 1 outbox row |

  **Mock mode is a wiring check, not the graded run.** Mock mode only has recorded responses for
  Delhivery, Blue Dart, Mamaearth and "Sharma Transport". It currently passes 6/12: healthy,
  reworded, lookalike, duplicate, no footprint and namesake. For companies with no recording, the
  mock client stands in a generic healthy company, so Tide, Mahindra Logistics and India Glycols
  look healthy in mock mode, which is meaningless for those cases. **The graded result comes from
  the live run** (`python -m crd.cli test --live`), and this table will be updated with it. Case 10
  passes when the failure is actually forced (`CRD_FAIL_SOURCES=enrich`).
- **Kill test** (re-run on the merged code and the real database): we started the server with
  `CRD_SLOW_EXECUTE=5`, approved a terms email over HTTP, hard-killed the server mid-send, and
  restarted. The action was in `executing` with 0 outbox rows. After restart it was `done` with
  **exactly 1 outbox row**, and `/outbox` shows "Exactly once".
- **Determinism:** case 11 repeats case 01 with different wording and must give the same rating.
  Scoring is a pure function of the evidence and the config.

## 11. Approaches we considered

1. **An LLM rates each company directly.** Rejected. It is not deterministic, it draws on the
   model's memory instead of cited evidence, and a credit decision cannot be explained or audited.
2. **A dashboard of raw signals.** Rejected. That is a tool, not a service: the analyst still does
   the review, the decision and the follow-up.
3. **Rules over cited evidence, the LLM for extraction only, and an approval gate.** Chosen.

What worked:
- the coverage gate (no GREEN on thin evidence);
- turning failed sources into HELD;
- identity-first resolution, which kills most namesake errors before scoring;
- idempotency keys plus reconcile, which made the kill demo boring, as it should be.

What didn't:
- Web Search news has no dates and returns Google redirect URLs, so we moved to Enrich `news[]` as
  the main news source.
- The registries sit behind CAPTCHAs, so we fall back to site search and otherwise report them as
  unavailable.
- The 5-per-minute rate limit forced batching of Identify.

**Priority as a separate layer.** We considered making importance part of the rating and rejected
it. The rating must stay a health score anyone can check. Importance changes the *action* instead:
- **sole-source carrier on AMBER:** backup RFQ now;
- **customer who relies on us on AMBER:** an account-owner call, because they tend to keep paying
  us;
- **customer who can switch easily:** a terms email at half the usual exposure bar;
- **strategic account:** a senior approver.

## 12. The hardest case

**A timeout that looks like "no news".** If IBBI or Enrich times out and we treat the empty answer
as "nothing found", a company in insolvency would come out GREEN. That is the worst possible error.
We handle it in three layers:
1. every failure becomes `Evidence(status="unavailable")`;
2. the coverage gate demands fresh evidence on the required signals;
3. any GREEN that relied on an unavailable source becomes **HELD** with "Missing: … Tried: …".

Case 10 forces this on purpose. The **lookalike** is a close second: Identify returns a 1.0 match
for "Sharma Transport" alongside four others, so a high score alone is not uniqueness.

## 13. What we left out and what comes next

- **Bank-statement and GST-filing signals** (payment delays, filing gaps). They need the
  counterparty's consent and an account-aggregator integration.
- **Carrier on-time performance from the TMS**, the strongest operational signal for suppliers.
- **A learned model on outcomes** (who actually defaulted or failed). Not now: there is no outcome
  data yet, and fixed rules over cited evidence are explainable and auditable. Once a few quarters
  of outcomes exist, the rule weights in `config.yaml` can be tuned against them.
- **Importance from data**, such as our share of the customer's shipments and years of relationship,
  instead of a column.

---

**Anything written before 10:15:** none of the code. The planning documents in `docs/`
(`TEAM_PLAN.md`, `ARTH_core.md`, `NILAY_data.md`, `PRACHI_product.md`) were written during the
event, after 10:15.
