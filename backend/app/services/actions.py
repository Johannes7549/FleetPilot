"""Action proposals, human approval and execution.

State machine (only these transitions are possible):

    pending_approval --approve--> executing --ok--> executed
                                            --err-> failed
    pending_approval --reject---> rejected
    pending_approval --ttl------> expired

Safety properties (each covered by tests/evals):
* proposals are created only by the server after tenant, permission, parameter and evidence checks;
* approval requires `actions:approve`, the same tenant, and the exact `payload_hash` shown to the
  approver; the hash is recomputed from stored fields so tampering invalidates the approval;
* the pending->executing transition is a compare-and-swap UPDATE, so concurrent or repeated
  approvals cannot execute an action twice; rejected/expired proposals can never execute;
* every attempt — accepted or refused — is written to the approval history and the audit log.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from app.analytics.fleet import FleetAnalysis
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, PermissionDeniedError, TenantAccessError, ValidationFailedError
from app.models import ActionApproval, ActionProposal
from app.repositories.telemetry import TelemetryRepository
from app.security.context import TenantContext
from app.services import action_policy as policy
from app.services.adapters import get_adapter
from app.services.audit import append_audit

ACTION_TYPES = ("create_upgrade_order", "open_remediation_ticket", "flag_device_for_replacement", "notify_employee")
OPEN_STATUSES = ("pending_approval", "executing")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def payload_hash(company_id: str, action_type: str, params: dict, evidence_ids: list[str]) -> str:
    return hashlib.sha256(canonical({"company_id": company_id, "action_type": action_type, "params": params,
                                     "evidence_ids": sorted(evidence_ids)}).encode()).hexdigest()


def idempotency_key(company_id: str, action_type: str, params: dict) -> str:
    return hashlib.sha256(canonical({"c": company_id, "a": action_type, "p": params}).encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class ActionService:
    def __init__(self, session: Session, ctx: TenantContext, settings: Settings | None = None) -> None:
        self.s = session
        self.ctx = ctx
        self.settings = settings or get_settings()
        self.repo = TelemetryRepository(session, ctx)

    # ------------------------------------------------------------------------------------------
    def propose(self, action_type: str, params: dict, *, agent_run_id: str | None = None,
                analysis: FleetAnalysis | None = None) -> tuple[ActionProposal, bool]:
        """Validate and persist a proposal. Returns (proposal, created). An identical open proposal is
        returned instead of creating a duplicate (idempotency)."""
        if not self.ctx.can("actions:propose"):
            raise PermissionDeniedError(f"role '{self.ctx.role}' may not propose actions")
        if action_type not in ACTION_TYPES:
            raise ValidationFailedError(f"unknown action type {action_type!r}")
        fa = analysis or FleetAnalysis(self.repo)
        device_id = params.get("device_id")
        employee_id = params.get("employee_id")

        if action_type == "notify_employee":
            self.repo.employee(employee_id)  # tenant check -> 404 if foreign/unknown
            support = policy.support_notification(fa, employee_id)
        else:
            dev = self.repo.device(device_id)  # tenant check
            employee_id = dev.employee_id
            if action_type == "create_upgrade_order":
                support = policy.support_upgrade(fa, device_id, params["component"])
            elif action_type == "open_remediation_ticket":
                support = policy.support_remediation(fa, device_id, params["check_id"])
            else:
                support = policy.support_replacement(fa, device_id)

        if not support.evidence:
            raise policy.InsufficientEvidenceError("no supporting telemetry evidence found")
        # Evidence must reference real snapshots in this tenant.
        snap_ids = {e.snapshot_id for e in support.evidence if e.snapshot_id}
        missing = snap_ids - self.repo.snapshot_ids_exist(snap_ids)
        if missing:
            raise policy.InsufficientEvidenceError(f"evidence references unknown snapshots: {sorted(missing)[:3]}")

        key = idempotency_key(self.ctx.company_id, action_type, params)
        existing = self.s.scalar(select(ActionProposal).where(and_(
            ActionProposal.idempotency_key == key, ActionProposal.company_id == self.ctx.company_id)))
        if existing is not None:
            return existing, False

        evidence = [e.model_dump(mode="json") for e in support.evidence]
        ev_ids = [e["evidence_id"] for e in evidence]
        now = datetime.now(UTC)
        proposal = ActionProposal(
            id=str(uuid.uuid4()), company_id=self.ctx.company_id, action_type=action_type, device_id=device_id,
            employee_id=employee_id, params=params, justification=support.justification, evidence=evidence,
            payload_hash=payload_hash(self.ctx.company_id, action_type, params, ev_ids), idempotency_key=key,
            status="pending_approval", proposed_by=self.ctx.user_id, agent_run_id=agent_run_id, created_at=now,
            expires_at=now + timedelta(hours=self.settings.approval_ttl_hours),
        )
        self.s.add(proposal)
        self.s.flush()
        append_audit(self.s, company_id=self.ctx.company_id, actor_id=self.ctx.user_id,
                     actor_type="agent" if agent_run_id else "user", event_type="action.proposed",
                     action_proposal_id=proposal.id, device_id=device_id,
                     details={"action_type": action_type, "params": params, "evidence_ids": ev_ids,
                              "payload_hash": proposal.payload_hash, "agent_run_id": agent_run_id,
                              "justification": support.justification})
        return proposal, True

    # ------------------------------------------------------------------------------------------
    def get(self, proposal_id: str) -> ActionProposal:
        p = self.s.scalar(select(ActionProposal).where(and_(
            ActionProposal.id == proposal_id, ActionProposal.company_id == self.ctx.company_id)))
        if p is None:
            raise TenantAccessError("action proposal not found")
        return p

    def list(self, status: str | None = None) -> list[ActionProposal]:
        self.expire_stale()
        q = select(ActionProposal).where(ActionProposal.company_id == self.ctx.company_id)
        if status:
            q = q.where(ActionProposal.status == status)
        return list(self.s.scalars(q.order_by(ActionProposal.created_at.desc())))

    def history(self, proposal_id: str) -> list[ActionApproval]:
        self.get(proposal_id)
        return list(self.s.scalars(select(ActionApproval).where(and_(
            ActionApproval.proposal_id == proposal_id, ActionApproval.company_id == self.ctx.company_id))
            .order_by(ActionApproval.id)))

    def expire_stale(self) -> int:
        now = datetime.now(UTC)
        stale = list(self.s.scalars(select(ActionProposal).where(and_(
            ActionProposal.company_id == self.ctx.company_id, ActionProposal.status == "pending_approval",
            ActionProposal.expires_at < now))))
        for p in stale:
            self._transition(p, "pending_approval", "expired", idempotency_key=None)
            append_audit(self.s, company_id=p.company_id, actor_id="system", actor_type="system",
                         event_type="action.expired", action_proposal_id=p.id, device_id=p.device_id, details={})
        return len(stale)

    def _transition(self, p: ActionProposal, from_status: str, to_status: str, **values: Any) -> bool:
        """Compare-and-swap on (status, version). Returns False if someone else won the race."""
        res = self.s.execute(
            update(ActionProposal)
            .where(and_(ActionProposal.id == p.id, ActionProposal.company_id == self.ctx.company_id,
                        ActionProposal.status == from_status, ActionProposal.version == p.version))
            .values(status=to_status, version=p.version + 1, **values)
            .execution_options(synchronize_session=False)
        )
        self.s.flush()
        if res.rowcount != 1:
            return False
        self.s.refresh(p)
        return True

    def _record(self, p: ActionProposal, decision: str, hash_seen: str, comment: str | None, outcome: str) -> None:
        self.s.add(ActionApproval(proposal_id=p.id, company_id=self.ctx.company_id, decision=decision,
                                  approver_id=self.ctx.user_id, payload_hash_seen=hash_seen or "",
                                  comment=comment, outcome=outcome))
        append_audit(self.s, company_id=self.ctx.company_id, actor_id=self.ctx.user_id, actor_type="user",
                     event_type=f"action.{decision}.{'accepted' if outcome == 'accepted' else 'refused'}",
                     action_proposal_id=p.id, device_id=p.device_id,
                     details={"outcome": outcome, "payload_hash_seen": hash_seen, "comment": comment})
        self.s.flush()

    def _check_decidable(self, p: ActionProposal, decision: str, hash_seen: str, comment: str | None) -> None:
        def refuse(reason: str, exc: type[Exception] = ConflictError) -> None:
            self._record(p, decision, hash_seen, comment, f"refused:{reason}")
            self.s.commit()  # persist the refusal trail even though we raise
            raise exc(f"{decision} refused: {reason.replace('_', ' ')}")

        if not self.ctx.can("actions:approve"):
            refuse("not_authorized", PermissionDeniedError)
        if p.status == "pending_approval" and _aware(p.expires_at) < datetime.now(UTC):
            self._transition(p, "pending_approval", "expired", idempotency_key=None)
            refuse("expired")
        if p.status != "pending_approval":
            refuse(f"status_is_{p.status}")
        recomputed = payload_hash(p.company_id, p.action_type, p.params, [e["evidence_id"] for e in p.evidence])
        if recomputed != p.payload_hash:
            refuse("proposal_modified_after_creation")
        if hash_seen != p.payload_hash:
            refuse("payload_hash_mismatch", ValidationFailedError)
        if decision == "approve" and self.settings.require_distinct_approver and p.proposed_by == self.ctx.user_id:
            refuse("approver_must_differ_from_proposer", PermissionDeniedError)

    # ------------------------------------------------------------------------------------------
    def approve(self, proposal_id: str, *, payload_hash_seen: str, comment: str | None = None) -> ActionProposal:
        p = self.get(proposal_id)
        self._check_decidable(p, "approve", payload_hash_seen, comment)
        if not self._transition(p, "pending_approval", "executing", decided_at=datetime.now(UTC),
                                decided_by=self.ctx.user_id):
            self._record(p, "approve", payload_hash_seen, comment, "refused:concurrent_decision")
            self.s.commit()
            raise ConflictError("approve refused: already decided by another request")
        self._record(p, "approve", payload_hash_seen, comment, "accepted")
        self.s.commit()  # the CAS is durable before any side effect runs
        return self._execute(p)

    def _execute(self, p: ActionProposal) -> ActionProposal:
        adapter = get_adapter()
        try:
            # Re-check the target still belongs to this tenant at execution time.
            if p.device_id:
                self.repo.device(p.device_id)
            result = adapter.execute(p.action_type, p.params, proposal_id=p.id, company_id=p.company_id)
            self._transition(p, "executing", "executed", executed_at=datetime.now(UTC), execution_result=result,
                             external_ref=result.get("external_ref"), idempotency_key=None)
            event, details = "action.executed", result
        except Exception as exc:  # noqa: BLE001
            self.s.rollback()
            p = self.get(p.id)
            details = {"error": f"{exc.__class__.__name__}: {exc}", "adapter": adapter.name}
            self._transition(p, "executing", "failed", executed_at=datetime.now(UTC), execution_result=details,
                             idempotency_key=None)
            event = "action.failed"
        append_audit(self.s, company_id=p.company_id, actor_id=self.ctx.user_id, actor_type="user", event_type=event,
                     action_proposal_id=p.id, device_id=p.device_id, details=details)
        self.s.commit()
        return p

    def reject(self, proposal_id: str, *, payload_hash_seen: str, comment: str | None = None) -> ActionProposal:
        p = self.get(proposal_id)
        self._check_decidable(p, "reject", payload_hash_seen, comment)
        if not self._transition(p, "pending_approval", "rejected", decided_at=datetime.now(UTC),
                                decided_by=self.ctx.user_id, idempotency_key=None):
            self._record(p, "reject", payload_hash_seen, comment, "refused:concurrent_decision")
            self.s.commit()
            raise ConflictError("reject refused: already decided by another request")
        self._record(p, "reject", payload_hash_seen, comment, "accepted")
        self.s.commit()
        return p
