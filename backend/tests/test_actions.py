"""Approval workflow safety properties."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.errors import (
    ConflictError,
    InsufficientEvidenceError,
    PermissionDeniedError,
    TenantAccessError,
    ValidationFailedError,
)
from app.models import ActionApproval, ActionProposal, AuditLog
from app.services.actions import ActionService
from app.services.adapters import FailingAdapter, SimulatedAdapter, set_adapter
from app.services.audit import verify_chain
from tests.conftest import ACME_ADMIN, ACME_OPERATOR, ACME_VIEWER, GLOBEX_ADMIN

STORAGE = ("create_upgrade_order", {"device_id": "8NM23J95R5I6", "component": "storage", "spec": "1 TB SSD"})


def propose(s, ctx=ACME_OPERATOR, action=STORAGE):
    p, created = ActionService(s, ctx).propose(*action)
    s.commit()
    return p, created


def test_proposal_is_pending_and_evidence_backed(fresh):
    p, created = propose(fresh)
    assert created and p.status == "pending_approval" and p.evidence
    assert all(e["company_id"] == "acme-001" for e in p.evidence)


def test_duplicate_proposal_is_reused(fresh):
    p1, _ = propose(fresh)
    p2, created = propose(fresh)
    assert p1.id == p2.id and not created


def test_approve_executes_exactly_once(fresh):
    p, _ = propose(fresh)
    svc = ActionService(fresh, ACME_ADMIN)
    done = svc.approve(p.id, payload_hash_seen=p.payload_hash)
    assert done.status == "executed" and done.external_ref.startswith("SIM-PO-")
    assert done.execution_result["simulated"] is True
    with pytest.raises(ConflictError):
        svc.approve(p.id, payload_hash_seen=p.payload_hash)
    executed = fresh.scalars(select(AuditLog).where(AuditLog.event_type == "action.executed")).all()
    assert len(executed) == 1


def test_rejected_action_never_executes(fresh):
    p, _ = propose(fresh)
    svc = ActionService(fresh, ACME_ADMIN)
    assert svc.reject(p.id, payload_hash_seen=p.payload_hash, comment="no").status == "rejected"
    with pytest.raises(ConflictError):
        svc.approve(p.id, payload_hash_seen=p.payload_hash)
    assert fresh.get(ActionProposal, p.id).executed_at is None


def test_operator_and_viewer_cannot_approve(fresh):
    p, _ = propose(fresh)
    for ctx in (ACME_OPERATOR, ACME_VIEWER):
        with pytest.raises(PermissionDeniedError):
            ActionService(fresh, ctx).approve(p.id, payload_hash_seen=p.payload_hash)
    refusals = fresh.scalars(select(ActionApproval).where(ActionApproval.proposal_id == p.id)).all()
    assert [r.outcome for r in refusals] == ["refused:not_authorized"] * 2


def test_viewer_cannot_propose(fresh):
    with pytest.raises(PermissionDeniedError):
        ActionService(fresh, ACME_VIEWER).propose(*STORAGE)


def test_other_tenant_cannot_see_or_approve(fresh):
    p, _ = propose(fresh)
    with pytest.raises(TenantAccessError):
        ActionService(fresh, GLOBEX_ADMIN).approve(p.id, payload_hash_seen=p.payload_hash)
    assert ActionService(fresh, GLOBEX_ADMIN).list() == []


def test_cannot_propose_for_other_tenant_device(fresh):
    with pytest.raises(TenantAccessError):
        ActionService(fresh, ACME_OPERATOR).propose("create_upgrade_order",
                                                    {"device_id": "JRZSGXVMKE6M", "component": "storage", "spec": "x"})


def test_hash_mismatch_refused(fresh):
    p, _ = propose(fresh)
    with pytest.raises(ValidationFailedError):
        ActionService(fresh, ACME_ADMIN).approve(p.id, payload_hash_seen="0" * 64)
    assert fresh.get(ActionProposal, p.id).status == "pending_approval"


def test_modified_proposal_cannot_be_approved(fresh):
    p, _ = propose(fresh)
    fresh.execute(update(ActionProposal).where(ActionProposal.id == p.id)
                  .values(params={**p.params, "spec": "8 TB"}))
    fresh.commit()
    fresh.expire_all()
    with pytest.raises(ConflictError, match="modified"):
        ActionService(fresh, ACME_ADMIN).approve(p.id, payload_hash_seen=p.payload_hash)


def test_expired_proposal_cannot_be_approved(fresh):
    p, _ = propose(fresh)
    fresh.execute(update(ActionProposal).where(ActionProposal.id == p.id)
                  .values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    fresh.commit()
    fresh.expire_all()
    with pytest.raises(ConflictError, match="expired"):
        ActionService(fresh, ACME_ADMIN).approve(p.id, payload_hash_seen=p.payload_hash)
    assert fresh.get(ActionProposal, p.id).status == "expired"


def test_insufficient_evidence_refused(fresh):
    svc = ActionService(fresh, ACME_OPERATOR)
    with pytest.raises(InsufficientEvidenceError):  # healthy disk
        svc.propose("create_upgrade_order", {"device_id": "1LYSSFD074BB", "component": "storage", "spec": "x"})
    with pytest.raises(InsufficientEvidenceError):  # no RAM pressure
        svc.propose("create_upgrade_order", {"device_id": "1LYSSFD074BB", "component": "memory", "spec": "x"})
    with pytest.raises(InsufficientEvidenceError):  # passing check
        svc.propose("open_remediation_ticket", {"device_id": "1LYSSFD074BB", "check_id": "disk_encryption", "note": "x"})
    with pytest.raises(InsufficientEvidenceError):  # desktop has no battery
        svc.propose("create_upgrade_order", {"device_id": "DLT35MHGTH2D", "component": "battery", "spec": "x"})
    with pytest.raises(InsufficientEvidenceError):  # low-risk device
        svc.propose("flag_device_for_replacement", {"device_id": "Q3VUITEGQ400", "reason": "x"})
    with pytest.raises(ValidationFailedError):
        svc.propose("open_remediation_ticket", {"device_id": "1LYSSFD074BB", "check_id": "firewall", "note": "x"})


def test_distinct_approver_policy(fresh):
    p, _ = propose(fresh, ACME_ADMIN)
    svc = ActionService(fresh, ACME_ADMIN)
    svc.settings = svc.settings.model_copy(update={"require_distinct_approver": True})
    with pytest.raises(PermissionDeniedError):
        svc.approve(p.id, payload_hash_seen=p.payload_hash)


def test_execution_failure_is_recorded(fresh):
    p, _ = propose(fresh)
    set_adapter(FailingAdapter())
    try:
        done = ActionService(fresh, ACME_ADMIN).approve(p.id, payload_hash_seen=p.payload_hash)
    finally:
        set_adapter(SimulatedAdapter())
    assert done.status == "failed" and "unavailable" in done.execution_result["error"]
    with pytest.raises(ConflictError):  # a failed action is not silently retried by re-approval
        ActionService(fresh, ACME_ADMIN).approve(p.id, payload_hash_seen=p.payload_hash)


def test_audit_chain_and_append_only(fresh):
    p, _ = propose(fresh)
    ActionService(fresh, ACME_ADMIN).approve(p.id, payload_hash_seen=p.payload_hash)
    assert verify_chain(fresh, "acme-001") == (True, None)
    entry = fresh.scalars(select(AuditLog)).first()
    entry.details = {"tampered": True}
    with pytest.raises(PermissionError):
        fresh.flush()
    fresh.rollback()
    # Tampering through raw SQL bypasses the ORM hook (PostgreSQL has a trigger) but is detected:
    fresh.execute(update(AuditLog.__table__).where(AuditLog.id == entry.id).values(details={"x": 1}))
    assert verify_chain(fresh, "acme-001")[0] is False
