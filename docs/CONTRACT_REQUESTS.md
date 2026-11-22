# Contract requests from P3 (Prachi) to P1 (Arth)

Prachi's code already works against stand-ins in `crd/fakes/`. These are the fields and
functions it expects from the real `crd/models.py`, `crd/db.py` and `config.yaml`. Until
they exist, everything degrades safely: unknown fields are ignored, and the
`importance` / `depends_on_us` values are read from `assessment.extra`.

## 1. `Counterparty`: two new input columns

| Field | Values | Default | Meaning |
|---|---|---|---|
| `importance` | `strategic` / `core` / `standard` | `standard` | How much this relationship matters to us |
| `depends_on_us` | `high` / `medium` / `low` / `na` | `na` | Customers only: how much they rely on us. High means they are likely to keep paying us in a downturn. |

For suppliers, `criticality` already implies a minimum importance: `sole_source` counts as
strategic, `primary` as core and `backup` as standard.

**Please also copy all three (`criticality`, `importance`, `depends_on_us`) into
`Assessment.extra`**, so the report and dashboard can show priority without the input row.

## 2. `Action`: three new fields

```python
priority: str | None = None      # "P1".."P4"
why: str | None = None           # one line: why this action was proposed
requires_senior: bool = False    # strategic accounts: irreversible actions need a senior approver
```

DB columns: `priority TEXT, why TEXT, requires_senior INTEGER DEFAULT 0`, stored by
`insert_action` and returned by `get_action` / `get_actions`.

## 3. `db.get_action_by_key(idempotency_key) -> Action | None`

This is used when `insert_action` returns False (the key already exists). Without it,
policy falls back to scanning `get_actions(None)`.

## 4. `config.yaml` additions

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

## Design note (for the README's "Approaches we considered")

The **rating stays a pure health score**: fixed rules over cited evidence, unchanged. The
**priority** is a separate layer: rating × importance (± dependence). It changes *how urgently*
and *how hard* we act, never the rating itself. The same AMBER rating can lead to backup quotes
for a sole-source carrier, an account-owner call for a customer that relies on us, or a terms
email for a customer that can switch easily.
