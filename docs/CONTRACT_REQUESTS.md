# Requests from P3 (Prachi) to P1 (Arth) and P2 (Nilay)

Everything on `prachi/product` already works against the real `crd/models.py` / `crd/db.py`
without these. They make the priority feature and two test cases complete. All changes are additive.

## For Arth (P1)

### 1. Counterparty: two optional input columns (priority feature)

Add two optional fields, either to `Counterparty` (allowed: "add optional fields only") or by
having `crd/inputs.py` copy the columns into `Assessment.extra`:

| Field | Values | Default | Meaning |
|---|---|---|---|
| `importance` | `strategic` / `core` / `standard` | `standard` | How much this relationship matters to us |
| `depends_on_us` | `high` / `medium` / `low` / `na` | `na` | Customers only: how much they rely on us. High means they are likely to keep paying us in a downturn |

```python
importance: Literal["strategic", "core", "standard"] = "standard"
depends_on_us: Literal["high", "medium", "low", "na"] = "na"
```

`crd/actions/priority.py` reads them from the Counterparty row (via `db.get_counterparty`) first,
then from `assessment.extra`. Without them, every row is treated as `standard`, but **suppliers still
get importance from `criticality`** (sole_source → strategic, primary → core). Please also add the two
columns to `data/input/sample_counterparties.csv`.

**`Action` needs no new fields.** Priority, the one-line "why" and senior sign-off are recomputed
from the same rules by `policy.context()`.

### 2. config.yaml (optional; these are the defaults in code)

```yaml
report:
  fields: [priority, display_name, role, rating, score, inr_at_risk, top_reason, sources, action_status]
actions:
  high_dependence_amber: account_call      # AMBER + depends_on_us high -> account-owner call, not a terms email
  low_dependence_exposure_factor: 0.5      # depends_on_us low -> terms email from 0.5 x min_exposure_inr
  senior_approval_for: [strategic]
priority:
  severity:   {RED: 3, AMBER: 2, HELD: 1.5, CANNOT_VERIFY: 1.5, GREEN: 0}
  importance: {strategic: 3, core: 2, standard: 1}
  dependence_shift: {high: -1, medium: 0, low: 1, na: 0}   # customers only
  tiers: {P1: 6, P2: 4, P3: 2}                              # urgency >= threshold; GREEN is always P4
```

### 3. `tests/run_cases.py`: apply a case's `env:` block (case 10)

`tests/cases/10_source_failure.yaml` has `env: {CRD_FAIL_SOURCES: enrich}`. In `run_case`, after
`case = yaml.safe_load(f)`:

```python
old_env = {k: os.environ.get(k) for k in (case.get("env") or {})}
os.environ.update({k: str(v) for k, v in (case.get("env") or {}).items()})
try:
    ...  # existing body
finally:
    for k, v in old_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
```

Also open the yaml with `encoding="utf-8"`, because the cases contain ₹ and the Windows default
codec fails on it.

### 4. Small Windows fixes in P1 tests

`tests/test_orchestrator.py:35` writes a CSV containing "₹" without `encoding="utf-8"`. On Windows
it fails unless `PYTHONUTF8=1`.

`scripts/demo_kill.sh` defaults to `data/input/counterparties.csv`, which doesn't exist; the file in
the repo is `sample_counterparties.csv`. For the approve-then-kill demo, see
`scripts/demo_kill_approval.sh` / `.ps1`.

## For Nilay (P2)

- `registries.py`: IBBI / MCA / GST. Until it lands, every would-be GREEN is HELD, and cases 01, 03
  and 11 cannot pass.
- `crustdata.py`: `raw_ref` as `path.as_posix()` (2 tests fail on Windows), and
  `read_text(encoding="utf-8")`. The web UI already copes with both slash styles.
- Fixtures for the case companies, if credits allow: `tide.co`, `mahindralogistics.com`,
  `indiaglycols.com`, "Quasar India Limited", "Tide Water Oil Co. (India) Ltd.".
