"""Append-only, hash-chained audit log. Each entry's hash covers the previous entry's hash, so any
retroactive modification of a stored row is detectable by `verify_chain`."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog


def _digest(prev: str | None, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{prev or ''}|{body}".encode()).hexdigest()


def append_audit(
    session: Session,
    *,
    company_id: str,
    actor_id: str,
    actor_type: str,
    event_type: str,
    action_proposal_id: str | None = None,
    device_id: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    prev = session.scalar(
        select(AuditLog.entry_hash).where(AuditLog.company_id == company_id).order_by(AuditLog.id.desc()).limit(1)
    )
    ts = datetime.now(UTC)
    payload = {"ts": ts.isoformat(), "company_id": company_id, "actor_id": actor_id, "actor_type": actor_type,
               "event_type": event_type, "action_proposal_id": action_proposal_id, "device_id": device_id,
               "details": details or {}}
    entry = AuditLog(ts=ts, company_id=company_id, actor_id=actor_id, actor_type=actor_type, event_type=event_type,
                     action_proposal_id=action_proposal_id, device_id=device_id, details=details or {},
                     prev_hash=prev, entry_hash=_digest(prev, payload))
    session.add(entry)
    session.flush()
    return entry


def verify_chain(session: Session, company_id: str) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_id)."""
    prev = None
    q = select(AuditLog).where(AuditLog.company_id == company_id).order_by(AuditLog.id)
    # populate_existing: always verify what is stored, never stale objects cached in the session.
    for row in session.scalars(q.execution_options(populate_existing=True)):
        ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=UTC)
        payload = {"ts": ts.isoformat(), "company_id": row.company_id, "actor_id": row.actor_id,
                   "actor_type": row.actor_type, "event_type": row.event_type,
                   "action_proposal_id": row.action_proposal_id, "device_id": row.device_id, "details": row.details}
        if row.prev_hash != prev or row.entry_hash != _digest(prev, payload):
            return False, row.id
        prev = row.entry_hash
    return True, None
