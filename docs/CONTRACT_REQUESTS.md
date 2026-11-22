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

### 3. `tests/run_cases.py`: apply a case's `env:` block (case 10). DONE by Prachi

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

### 4. Small Windows fixes

The P1 tests are fixed (ea36e51). Still open: `python -m crd.cli review ...` crashes at the very end
on a Windows console when it prints "₹" (cp1252). The run itself completes. Fix: call
`sys.stdout.reconfigure(encoding="utf-8")` at CLI start, or print "Rs".

`scripts/demo_kill.sh` defaults to `data/input/counterparties.csv`, which doesn't exist; the file in
the repo is `sample_counterparties.csv`. For the approve-then-kill demo, see
`scripts/demo_kill_approval.sh` / `.ps1`.

## For Nilay (P2)

All done. Registries (IBBI works), Windows fixes, people, the watcher, and the 12 case companies
recorded live (mock: 12/12).

Changed by Prachi in `crd/data/crustdata.py` (it blocked the graded
run): in mock mode, an `enrich` call with no recording now raises
`SourceUnavailable("mock: no recorded company data for ...")` instead of patching
`_default.json` into a healthy stand-in company. A fake company can therefore never be rated GREEN.
The old stand-in is still available with `CRD_MOCK_SYNTHETIC=1`, which the three wiring tests that
rely on it (`test_p2_pipeline.py`, `test_signals.py::test_unknown_mock_company_flows`,
`test_crustdata_client.py::test_mock_unknown_name_is_not_found_and_unknown_domain_flows`) now set.
A new test, `test_mock_unknown_domain_has_no_fake_company_data`, guards the default.
