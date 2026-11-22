# Arth's handoff: where P1 Core stands (14:10, 22 Nov)

> For the Claude Code session running on Arth's laptop in `C:\Users\Admin\Desktop\trust_issues`.
> Read `CLAUDE.md`, `docs/ARTH_core.md` (my brief) and `docs/TEAM_PLAN.md` first. I am Arth, P1 Core, the integrator.

## Done and on `main`
- **Contracts** (`crd/models.py`, `crd/db.py`, `crd/config.py`, `config.yaml`). Nilay's and Prachi's files are stubs with the exact signatures.
- **Input adapter** (`crd/inputs.py`): CSV/JSON/XLSX input; header aliases; ₹ values like "42 L" / "1.2 Cr"; bad rows are reported without stopping the run.
- **Resolve** (`crd/resolve.py`):
  - Order: domain → CIN/GSTIN → name.
  - Identify calls are **batched**, because our key allows only 5 identify calls per minute: one call for all domains, then one for the names a domain did not settle.
  - A name match counts as unique only if the top match is 1.0 *and* the next is below `identity.name_second_max` (0.9).
  - Dedupe by company id, CIN, PAN (from the GSTIN) or domain; never by name alone.
- **Scoring** (`crd/scoring.py`): a pure function with these rules:
  - Hard stops give RED.
  - The coverage gate turns a GREEN without enough fresh evidence into CANNOT_VERIFY.
  - A GREEN that relied on a source that was unavailable becomes HELD.
  - Acquisition or merger news neutralises a headcount drop.
  - Namesake news is ignored.
  - Stale evidence is shown but not scored.
- **Explain** (`crd/explain.py`): the LLM text is checked against the facts, with a template fallback. `why()` gives the score breakdown.
- **Request parser** (`crd/request_parser.py`): rule-based first. A name that matches no counterparty produces a clarification question instead of a guess.
- **Orchestrator** (`crd/orchestrator.py`):
  - Step checkpoints: resolve, gather, score, propose.
  - `resume()` reconciles executing actions first, then continues the last unfinished run.
  - Time and credit caps stop a run cleanly with status `partial`.
  - Also provides `run_review_async`, `run_single` and `run_progress`.
- **CLI** (`crd/cli.py`): init, review, resume, status, why, actions, approve, reject, execute, costs, test.
- **Tests:**
  - `tests/test_scoring.py`, `tests/test_resolve.py`, `tests/test_orchestrator.py` (includes a simulated kill -9 and resume).
  - `tests/test_e2e_fixtures.py`: the full pipeline on Nilay's real recorded responses, using a **test-only** stand-in in `tests/standin.py`.
  - `tests/run_cases.py` runs and grades `tests/cases/*.yaml`.
  - 27 tests pass: `python -m pytest -q tests`.
- **Demo:** `scripts/demo_kill.sh` rehearses the kill on stage.

## Facts from Nilay's probe (`tests/fixtures/README.md`)
- Rate limit is **5/min** for identify, enrich, job_search and person_enrich, and 10/min for web. Hence the batching and `limits.max_minutes: 25`.
- Enrich returns no `updated_at`. Use the last `headcount.timeseries[].date` as the record date.
- Paths: web search is `/web/search/live`; web fetch is `/web/enrich/live`. Web Search news has no dates and returns google redirect URLs, so use Enrich `news[]`.
- `employee_reviews.overall_rating` is `{rating, total_count, ...}`. Scoring expects Evidence value `{"rating", "count"}`; Nilay's signals.py maps it.

## Waiting on teammates
- **Nilay:** the real `crd/data/crustdata.py`, `signals.py`, `news.py`, `registries.py`, `people.py`, `watcher.py` and `crd/llm.py`.
- **Prachi:** `crd/actions/policy.py`, `executor.py`, `channels.py`, `crd/report/render.py`, `crd/web/app.py`, `tests/cases/*.yaml`, README.

## What to do when their code lands (the integration loop)
1. `git fetch origin` and look at the new branches and commits (`git log --oneline --all -15`).
2. Merge each branch into `main`. Nilay's history may be unrelated, so use `--allow-unrelated-histories` if needed. Resolve conflicts keeping each owner's version of their own files.
3. `python -m pytest -q tests`, then `python -c "import crd.web.app, crd.orchestrator"`.
4. Mock run: `python -m crd.cli review "Run this week's risk review on all our carriers and customers" --input data/input/sample_counterparties.csv --mock`.
5. Live run (needs `CRUSTDATA_API_KEY` in `.env`; never commit `.env`): the same command with `--live`, on 3–5 real counterparties. Check: every fact has a source and a date; Sharma Transport → CANNOT_VERIFY; ledger costs appear.
6. Fix breakages at the seams. Only edit P1 files; for anything else, write the exact change for the owner.
7. `git push origin main` and post in the team chat what was merged.

## Remaining P1 tasks (ARTH_core.md)
- The hour-3 requirement change (ask Arth what was announced), usually a `config.yaml` key or a new `Assessment.extra` field plus `report.fields`.
- Adapt `crd/inputs.py` to the kickoff kit's counterparty file in `data/input/`.
- Practise the live-fix types: new output field, threshold, news category, cost cap, new hard stop.
- 16:30 full test-case run and triage; 17:15 freeze; 17:45 `git tag v1`.

## Timeline
14:30 checkpoint submission (Prachi) · 15:30 live mode on 5 counterparties · 16:30 full test-case run · 17:15 feature freeze · 18:00 code freeze.
