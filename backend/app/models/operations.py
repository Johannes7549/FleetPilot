"""Operational entities: users, action proposals/approvals, audit log, agent runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType, TZDateTime, utcnow


class User(Base):
    """An IT administrator (application user) — distinct from fleet `employees`."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    # viewer: read-only; operator: read + propose actions; admin: + approve/reject + settings
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class ActionProposal(Base):
    __tablename__ = "action_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    employee_id: Mapped[str | None] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSONType)
    justification: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONType)
    # sha256 over the canonical (company, action, params, evidence ids). An approval must quote it.
    payload_hash: Mapped[str] = mapped_column(String(64))
    # Prevents duplicate *open* proposals for the same action; cleared when closed.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    proposed_by: Mapped[str] = mapped_column(String(64))
    agent_run_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    execution_result: Mapped[dict | None] = mapped_column(JSONType)
    external_ref: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)


class ActionApproval(Base):
    """Every approve/reject decision (including refused attempts) — the approval history."""

    __tablename__ = "action_approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"), index=True)
    company_id: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # approve | reject
    approver_id: Mapped[str] = mapped_column(String(64))
    payload_hash_seen: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(32))  # accepted | refused:<reason>
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class AuditLog(Base):
    """Append-only, hash-chained audit trail (per company).

    Updates/deletes are blocked by an ORM hook here and by a trigger in PostgreSQL (migration).
    """

    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    company_id: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    actor_type: Mapped[str] = mapped_column(String(16))  # user | agent | system
    event_type: Mapped[str] = mapped_column(String(64))
    action_proposal_id: Mapped[str | None] = mapped_column(String(36), index=True)
    device_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (Index("ix_audit_company_ts", "company_id", "ts"),)


@event.listens_for(AuditLog, "before_update")
def _audit_no_update(*_args) -> None:
    raise PermissionError("audit_logs is append-only")


@event.listens_for(AuditLog, "before_delete")
def _audit_no_delete(*_args) -> None:
    raise PermissionError("audit_logs is append-only")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    question: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str | None] = mapped_column(String(32))
    plan: Mapped[list | None] = mapped_column(JSONType)
    response: Mapped[dict | None] = mapped_column(JSONType)
    token_usage: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class AgentStep(Base):
    """Observable execution timeline entry (no hidden reasoning is stored)."""

    __tablename__ = "agent_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    input: Mapped[dict | None] = mapped_column(JSONType)
    output: Mapped[dict | None] = mapped_column(JSONType)
    evidence_ids: Mapped[list | None] = mapped_column(JSONType)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("run_id", "seq"),)
