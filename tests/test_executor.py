"""Exactly-once tests for the approval gate + executor (PRACHI_product.md section 7.3)."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRD_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("CRD_DB_PATH", str(tmp_path / "test.db"))  # the real crd.db reads this
    monkeypatch.setenv("CRD_VAR", str(tmp_path / "var"))
    monkeypatch.setenv("CRD_FAKE_SEED", "0")
    monkeypatch.setenv("CRD_SLOW_EXECUTE", "0")
    monkeypatch.setenv("TEAM_INBOX", "team@example.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    from crd.deps import db

    db.init_db()
    return db


def _red_customer(run_id="run_t"):
    from crd.deps import models

    a = models.Assessment(
        run_id=run_id, cp_ids=["C-900"], display_name="Test Retail", role="customer",
        entity=models.ResolvedEntity(cp_ids=["C-900"], status="resolved"),
        rating=models.Rating.RED, score=30, exposure_inr=3_000_000, inr_at_risk=2_000_000,
        signals=[models.SignalScore(signal="news", points=-20, evidence_ids=[], note="layoffs reported")],
        explanation="Test.",
    )
    cp = models.Counterparty(cp_id="C-900", name="Test Retail", role="customer",
                             exposure_inr=3_000_000, credit_terms_days=45) \
        if hasattr(models, "Counterparty") else None
    return a, cp


def _proposed_email(db):
    from crd.actions import policy
    from crd.deps import config

    a, cp = _red_customer()
    acts = policy.propose(a, cp, config.get(), "rv_test")
    email = next(x for x in acts if x.type.value == "customer_terms_email")
    assert email.status.value == "pending_approval"
    return email


def _approve(db, act, draft=None):
    from crd.actions.policy import sha256

    draft = act.draft if draft is None else draft
    assert db.transition_action(act.action_id, "pending_approval", "approved", draft=draft,
                                draft_hash=sha256(draft), approved_by="tester",
                                approved_at=datetime.now(timezone.utc).isoformat())
    return db.get_action(act.action_id)


def _email_rows(db, key):
    return [m for m in db.list_outbox() if m["idempotency_key"] == key]


# 1 ---------------------------------------------------------------------------
def test_execute_twice_sends_once(env):
    from crd.actions import executor

    db = env
    act = _approve(db, _proposed_email(db))
    executor.execute_approved()
    executor.execute_approved()
    assert len(_email_rows(db, act.idempotency_key)) == 1
    assert db.get_action(act.action_id).status.value == "done"


# 2 ---------------------------------------------------------------------------
def test_crash_after_channel_write_reconciles_to_done(env, monkeypatch):
    from crd.actions import executor

    db = env
    act = _approve(db, _proposed_email(db))
    real = db.transition_action

    def crashing(action_id, frm, to, **kw):
        if frm == "executing" and to == "done":
            raise RuntimeError("killed after channel write")
        return real(action_id, frm, to, **kw)

    monkeypatch.setattr(executor.db, "transition_action", crashing)
    with pytest.raises(RuntimeError):
        executor.execute_one(db.get_action(act.action_id))
    monkeypatch.setattr(executor.db, "transition_action", real)

    assert db.get_action(act.action_id).status.value == "executing"
    executor.reconcile()
    assert db.get_action(act.action_id).status.value == "done"
    executor.execute_approved()
    assert len(_email_rows(db, act.idempotency_key)) == 1


# 3 ---------------------------------------------------------------------------
def test_crash_before_channel_write_reconciles_then_sends_once(env):
    from crd.actions import executor

    db = env
    act = _approve(db, _proposed_email(db))
    assert db.transition_action(act.action_id, "approved", "executing")  # killed here
    assert _email_rows(db, act.idempotency_key) == []

    executor.reconcile()
    assert db.get_action(act.action_id).status.value == "approved"
    executor.execute_approved()
    executor.execute_approved()
    assert len(_email_rows(db, act.idempotency_key)) == 1
    assert db.get_action(act.action_id).status.value == "done"


# 4 ---------------------------------------------------------------------------
def test_draft_edited_after_approval_goes_back_to_pending(env):
    from crd.actions import executor

    db = env
    act = _approve(db, _proposed_email(db))
    # someone changes the draft without re-approving (hash no longer matches)
    db.transition_action(act.action_id, "approved", "approved", draft=act.draft + "\nP.S. extra")
    executor.execute_approved()
    after = db.get_action(act.action_id)
    assert after.status.value == "pending_approval"
    assert "draft changed" in (after.error or "")
    assert _email_rows(db, act.idempotency_key) == []


# 5 ---------------------------------------------------------------------------
def test_two_threads_send_once(env, monkeypatch):
    from crd.actions import executor

    db = env
    act = _approve(db, _proposed_email(db))
    # bypass the in-process lock to prove the compare-and-set alone is enough
    monkeypatch.setattr(executor, "_lock", threading.Lock())
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        for a in db.get_actions("approved"):
            executor.execute_one(a)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(_email_rows(db, act.idempotency_key)) == 1
    assert db.get_action(act.action_id).status.value == "done"


# 6 ---------------------------------------------------------------------------
def test_duplicate_propose_makes_no_second_action(env):
    from crd.actions import policy
    from crd.deps import config

    db = env
    a, cp = _red_customer()
    first = policy.propose(a, cp, config.get(), "rv_test")
    second = policy.propose(a, cp, config.get(), "rv_test")
    assert [x.action_id for x in first] == [x.action_id for x in second]
    keys = [x.idempotency_key for x in db.get_actions(None)]
    assert len(keys) == len(set(keys)) == len(first)


# extra ------------------------------------------------------------------------
def test_unapproved_irreversible_never_runs(env):
    from crd.actions import executor

    db = env
    act = _proposed_email(db)  # pending_approval, not approved
    executor.execute_approved()
    assert _email_rows(db, act.idempotency_key) == []
    assert db.get_action(act.action_id).status.value == "pending_approval"


def test_cannot_verify_proposes_only_internal_alert(env):
    from crd.actions import policy
    from crd.deps import config, models

    a, cp = _red_customer()
    a.rating = models.Rating.CANNOT_VERIFY
    a.cannot_verify = ["CANNOT VERIFY: which company. Missing: domain. Tried: Identify."]
    acts = policy.propose(a, cp, config.get(), "rv_cv")
    assert [x.type.value for x in acts] == ["internal_alert"]
