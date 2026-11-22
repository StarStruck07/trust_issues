"""Priority = how healthy they are (rating) × how much the relationship matters (importance).

The rating stays a pure, sourced health score. Priority is a separate, explainable layer
that decides *how urgently* and *how hard* we act:

    urgency = severity(rating) × weight(importance) + dependence shift (customers only)

* Suppliers: a sole-source carrier counts as strategic, a primary as core, a backup as
  standard — unless the row says something more important.
* Customers: a customer who depends heavily on us tends to keep paying us in a downturn,
  so urgency drops one step; a customer who can drop us easily moves one step up.

All weights and thresholds come from ``cfg.priority`` (defaults below), so tuning is a
config change.
"""
from __future__ import annotations

from crd.deps import cfg_get

SUPPLIER_ROLES = {"carrier", "vendor", "warehouse_lessor"}
LEVELS = ["standard", "core", "strategic"]
CRITICALITY_TO_IMPORTANCE = {"sole_source": "strategic", "primary": "core", "backup": "standard"}

DEFAULTS = {
    "severity": {"RED": 3, "AMBER": 2, "HELD": 1.5, "CANNOT_VERIFY": 1.5, "GREEN": 0},
    "importance": {"strategic": 3, "core": 2, "standard": 1},
    "dependence_shift": {"high": -1, "medium": 0, "low": 1, "na": 0},
    "tiers": {"P1": 6, "P2": 4, "P3": 2},
}
TIER_LABEL = {"P1": "Act now", "P2": "This week", "P3": "Monitor", "P4": "No action"}


def _table(cfg, name: str) -> dict:
    sect = cfg_get(cfg_get(cfg, "priority"), name)
    if sect is None:
        return DEFAULTS[name]
    d = sect if isinstance(sect, dict) else vars(sect)
    return {**DEFAULTS[name], **d}


def _val(v):
    return getattr(v, "value", v)


def profile(a=None, cp=None) -> dict:
    """role / criticality / importance / depends_on_us, from the Counterparty row if we
    have it, else from ``assessment.extra`` (where the orchestrator copies them)."""
    extra = (getattr(a, "extra", None) or {}) if a is not None else {}

    def pick(key, default):
        v = getattr(cp, key, None) if cp is not None else None
        return v if v not in (None, "") else extra.get(key, default)

    role = _val(pick("role", getattr(a, "role", "customer") if a is not None else "customer"))
    return {
        "role": role,
        "criticality": pick("criticality", "na"),
        "importance": pick("importance", "standard"),
        "depends_on_us": pick("depends_on_us", "na"),
    }


def effective_importance(p: dict) -> str:
    imp = p.get("importance") or "standard"
    if imp not in LEVELS:
        imp = "standard"
    if p.get("role") in SUPPLIER_ROLES:
        from_crit = CRITICALITY_TO_IMPORTANCE.get(p.get("criticality"), "standard")
        if LEVELS.index(from_crit) > LEVELS.index(imp):
            imp = from_crit
    return imp


def assess(rating, p: dict, cfg=None) -> dict:
    """Returns {tier, label, urgency, importance, reason} for one counterparty."""
    rating = _val(rating)
    sev, w = _table(cfg, "severity"), _table(cfg, "importance")
    shift, tiers = _table(cfg, "dependence_shift"), _table(cfg, "tiers")
    imp = effective_importance(p)
    urgency = sev.get(rating, 0) * w.get(imp, 1)
    dep = p.get("depends_on_us") or "na"
    is_customer = p.get("role") == "customer"
    if is_customer and urgency > 0:
        urgency += shift.get(dep, 0)
    if rating == "GREEN":
        tier = "P4"
    elif urgency >= tiers["P1"]:
        tier = "P1"
    elif urgency >= tiers["P2"]:
        tier = "P2"
    elif urgency >= tiers["P3"]:
        tier = "P3"
    else:
        tier = "P4" if rating not in ("HELD", "CANNOT_VERIFY") else "P3"

    bits = [rating.replace("_", " "), imp]
    if p.get("role") in SUPPLIER_ROLES and p.get("criticality") not in (None, "", "na"):
        bits[1] = f"{imp} ({p['criticality'].replace('_', ' ')})"
    reason = " × ".join(bits)
    if is_customer and dep in ("high", "low") and rating != "GREEN":
        reason += ("; they depend on us, likely to keep paying" if dep == "high"
                   else "; they can switch easily, act sooner")
    return {"tier": tier, "label": TIER_LABEL[tier], "urgency": urgency, "importance": imp,
            "depends_on_us": dep, "reason": reason}


def for_assessment(a, cp=None, cfg=None) -> dict:
    if cp is None:  # real db keeps the input row; fall back to assessment.extra if it doesn't
        from crd.deps import db

        getter = getattr(db, "get_counterparty", None)
        try:
            cp = getter(a.cp_ids[0]) if getter else None
        except Exception:
            cp = None
    return assess(a.rating, profile(a, cp), cfg)
