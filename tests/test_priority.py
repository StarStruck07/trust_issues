"""Priority (rating × importance) and the importance-aware action rules."""
from __future__ import annotations

import pytest

from crd.actions import priority


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRD_DB", str(tmp_path / "p.db"))
    monkeypatch.setenv("CRD_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setenv("CRD_VAR", str(tmp_path / "var"))
    monkeypatch.setenv("CRD_FAKE_SEED", "0")
    from crd.deps import db

    db.init_db()
    return db


def _a(rating, role="customer", exposure=3_000_000, cp_id="C-1", **profile):
    """profile (importance, depends_on_us) travels in assessment.extra, as the orchestrator copies it."""
    from crd.deps import models

    return models.Assessment(
        run_id="run_p", cp_ids=[cp_id], display_name="Acme", role=role, rating=models.Rating(rating),
        entity=models.ResolvedEntity(cp_ids=[cp_id], status="resolved"), extra=dict(profile),
        score=40, exposure_inr=exposure,
        signals=[models.SignalScore(signal="news", points=-10, evidence_ids=[], note="layoffs reported")],
        explanation="Test.",
    )


def _cp(role="customer", cp_id="C-1", criticality="na"):
    from crd.deps import models

    return models.Counterparty(cp_id=cp_id, name="Acme", role=role, credit_terms_days=30, criticality=criticality)


def _types(acts):
    return sorted((x.type.value, x.idempotency_key.endswith(":account_call")) for x in acts)


# ---------------------------------------------------------------- priority math
def test_rating_unchanged_priority_differs_by_importance():
    hi = priority.assess("AMBER", {"role": "customer", "importance": "strategic", "depends_on_us": "na"})
    lo = priority.assess("AMBER", {"role": "customer", "importance": "standard", "depends_on_us": "na"})
    assert hi["tier"] == "P1" and lo["tier"] == "P3"


def test_sole_source_supplier_counts_as_strategic():
    p = priority.assess("AMBER", {"role": "carrier", "criticality": "sole_source", "importance": "standard"})
    assert p["importance"] == "strategic" and p["tier"] == "P1"
    b = priority.assess("RED", {"role": "carrier", "criticality": "backup", "importance": "standard"})
    assert b["importance"] == "standard" and b["tier"] == "P3"


def test_customer_dependence_shifts_urgency():
    base = {"role": "customer", "importance": "core"}
    high = priority.assess("AMBER", {**base, "depends_on_us": "high"})
    low = priority.assess("AMBER", {**base, "depends_on_us": "low"})
    assert high["urgency"] < low["urgency"]
    assert "keep paying" in high["reason"] and "act sooner" in low["reason"]


def test_green_is_always_p4_and_unknowns_never_ignored():
    assert priority.assess("GREEN", {"role": "customer", "importance": "strategic"})["tier"] == "P4"
    assert priority.assess("CANNOT_VERIFY", {"role": "carrier", "importance": "standard"})["tier"] == "P3"


# ---------------------------------------------------------------- policy
def test_amber_customer_who_relies_on_us_gets_a_call_not_a_terms_email(env):
    from crd.actions import policy
    from crd.deps import config

    acts = policy.propose(_a("AMBER", importance="core", depends_on_us="high"), _cp(), config.get(), "rv")
    types = _types(acts)
    assert ("customer_terms_email", False) not in types
    assert ("internal_alert", True) in types  # the account-owner call
    call = next(x for x in acts if x.idempotency_key.endswith(":account_call"))
    assert call.reversible and call.status.value == "approved"


def test_easily_replaced_customer_gets_terms_email_below_usual_bar(env):
    from crd.actions import policy
    from crd.deps import config

    a = _a("AMBER", exposure=600_000, importance="core", depends_on_us="medium")  # below ₹10 L, above ₹5 L
    usual = policy.propose(a, _cp(cp_id="C-1"), config.get(), "rv1")
    assert "customer_terms_email" not in [x.type.value for x in usual]
    a2 = _a("AMBER", exposure=600_000, cp_id="C-2", importance="core", depends_on_us="low")
    easy = policy.propose(a2, _cp(cp_id="C-2"), config.get(), "rv1")
    email = next(x for x in easy if x.type.value == "customer_terms_email")
    plan = policy.plan(a2, _cp(cp_id="C-2"), config.get())
    why = next(p["why"] for p in plan if p["type"].value == "customer_terms_email")
    assert email and "switch easily" in why


def test_strategic_account_needs_senior_approval(env):
    from crd.actions import policy
    from crd.deps import config

    a = _a("RED", importance="strategic", depends_on_us="medium")
    cp = _cp()
    acts = policy.propose(a, cp, config.get(), "rv")
    irrev = [x for x in acts if not x.reversible]
    assert irrev and all(policy.context(x, a, cp)["requires_senior"] for x in irrev)
    assert all(not policy.context(x, a, cp)["requires_senior"] for x in acts if x.reversible)


def test_strategic_supplier_gets_backup_rfq_even_if_marked_backup(env):
    from crd.actions import policy
    from crd.deps import config

    cp = _cp(role="vendor", criticality="backup")
    a = _a("AMBER", role="vendor", importance="strategic")
    acts = policy.propose(a, cp, config.get(), "rv")
    rfq = next(x for x in acts if x.type.value == "backup_rfq")
    ctx = policy.context(rfq, a, cp)
    assert ctx["priority"] == "P1" and ctx["why"]


def test_senior_gate_enforced_on_approve(env, monkeypatch):
    from fastapi.testclient import TestClient

    from crd.actions import policy
    from crd.deps import config
    from crd.web.app import app

    from crd.deps import db

    a = _a("RED", importance="strategic")
    db.save_assessment(a)  # context() looks the assessment up at approval time
    acts = policy.propose(a, _cp(), config.get(), "rv")
    hold = next(x for x in acts if x.type.value == "credit_hold")
    monkeypatch.setattr("crd.web.app._bg", lambda *a, **k: None)  # don't execute in background
    with TestClient(app) as c:
        r = c.post(f"/approvals/{hold.action_id}", data={"decision": "approve", "approver": "Asha",
                                                         "draft": hold.draft}, follow_redirects=False)
        assert "err=senior" in r.headers["location"]
        assert env.get_action(hold.action_id).status.value == "pending_approval"
        r = c.post(f"/approvals/{hold.action_id}", data={"decision": "approve", "approver": "Asha",
                                                         "draft": hold.draft, "senior": "yes"},
                   follow_redirects=False)
        assert "done=" in r.headers["location"]
        after = env.get_action(hold.action_id)
        assert after.status.value == "approved" and after.approved_by == "Asha (senior)"
