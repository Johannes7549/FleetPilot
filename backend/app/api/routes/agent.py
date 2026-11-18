"""Copilot, agent runs (execution timeline), actions/approvals and audit log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.config import get_settings
from app.db.session import get_db
from app.models import AgentRun, AgentStep, AuditLog
from app.security.auth import current_tenant, rate_limiter, require
from app.security.context import TenantContext
from app.services.actions import ActionService
from app.services.audit import verify_chain
from app.tools import ToolContext, registry

router = APIRouter(prefix="/api")


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(None, max_length=36)


@router.post("/copilot/ask")
def ask(body: AskIn, ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    rate_limiter.check(f"ask:{ctx.user_id}", get_settings().rate_limit_per_minute)
    history = []
    if body.conversation_id:
        for r in db.scalars(select(AgentRun).where(and_(AgentRun.conversation_id == body.conversation_id,
                                                        AgentRun.company_id == ctx.company_id))
                            .order_by(AgentRun.created_at).limit(10)):
            history += [{"role": "user", "content": r.question}, _assistant_turn(r.response or {})]
    return run_agent(db, ctx, body.question, conversation_id=body.conversation_id, history=history)


def _assistant_turn(resp: dict) -> dict:
    """Previous answer as conversation context: text plus the concrete targets it referenced, so follow-ups
    ("what about its battery?") can be resolved without re-asking. Only this tenant's runs are loaded."""
    devices = list(dict.fromkeys(e["device_id"] for e in resp.get("evidence") or [] if e.get("device_id")))
    employees = list(dict.fromkeys((p.get("params") or {}).get("employee_id") for p in resp.get("proposals") or []
                                   if (p.get("params") or {}).get("employee_id")))
    return {"role": "assistant", "content": resp.get("answer", ""), "devices": devices[:20],
            "employees": employees[:10], "tools": [t["tool"] for t in resp.get("tools_used") or []][:8]}


def _run_dict(r: AgentRun, steps: list[AgentStep] | None = None) -> dict[str, Any]:
    d = {"run_id": r.id, "question": r.question, "intent": r.intent, "status": r.status, "provider": r.provider,
         "conversation_id": r.conversation_id, "user_id": r.user_id, "created_at": r.created_at,
         "duration_ms": r.duration_ms, "token_usage": r.token_usage, "plan": r.plan, "error": r.error,
         "execution": (r.response or {}).get("execution")}
    if steps is not None:
        d["response"] = r.response
        d["steps"] = [{"seq": s.seq, "step_type": s.step_type, "name": s.name, "status": s.status, "input": s.input,
                       "output": s.output, "evidence_ids": s.evidence_ids, "duration_ms": s.duration_ms,
                       "started_at": s.started_at} for s in steps]
    return d


@router.get("/agent-runs")
def runs(limit: int = Query(50, ge=1, le=200), conversation_id: str | None = None,
         ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    q = select(AgentRun).where(AgentRun.company_id == ctx.company_id)
    if conversation_id:
        q = q.where(AgentRun.conversation_id == conversation_id)
    rows = db.scalars(q.order_by(AgentRun.created_at.desc()).limit(limit))
    return [_run_dict(r) | ({"response": r.response} if conversation_id else {}) for r in rows]


@router.get("/agent-runs/{run_id}")
def run_detail(run_id: str, ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    r = db.scalar(select(AgentRun).where(and_(AgentRun.id == run_id, AgentRun.company_id == ctx.company_id)))
    if r is None:
        raise HTTPException(404, "run not found")
    steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq)))
    return _run_dict(r, steps)


# ---- actions ---------------------------------------------------------------------------------
def _p(p, history=None) -> dict:
    d = {"id": p.id, "action_type": p.action_type, "device_id": p.device_id, "employee_id": p.employee_id,
         "params": p.params, "justification": p.justification, "evidence": p.evidence, "payload_hash": p.payload_hash,
         "status": p.status, "proposed_by": p.proposed_by, "agent_run_id": p.agent_run_id, "created_at": p.created_at,
         "expires_at": p.expires_at, "decided_at": p.decided_at, "decided_by": p.decided_by,
         "executed_at": p.executed_at, "execution_result": p.execution_result, "external_ref": p.external_ref}
    if history is not None:
        d["history"] = [{"decision": h.decision, "approver_id": h.approver_id, "outcome": h.outcome,
                         "comment": h.comment, "created_at": h.created_at, "payload_hash_seen": h.payload_hash_seen}
                        for h in history]
    return d


@router.get("/actions")
def list_actions(status: str | None = None, ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    svc = ActionService(db, ctx)
    rows = [_p(p) for p in svc.list(status)]
    db.commit()
    return rows


@router.get("/actions/{proposal_id}")
def get_action(proposal_id: str, ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    svc = ActionService(db, ctx)
    return _p(svc.get(proposal_id), svc.history(proposal_id))


class ProposeIn(BaseModel):
    action_type: str
    params: dict


@router.post("/actions")
def propose(body: ProposeIn, ctx: TenantContext = Depends(require("actions:propose")), db: Session = Depends(get_db)):
    """Manual proposal (e.g. from an insight's recommended action). Same validation as the agent path."""
    spec = registry.get(body.action_type)
    if spec is None or spec.kind != "action":
        raise HTTPException(422, "unknown action type")
    res = registry.execute(body.action_type, body.params, ToolContext(session=db, tenant=ctx))
    if not res.ok:
        db.rollback()
        code = {"not_found": 404, "permission_denied": 403, "insufficient_evidence": 422,
                "invalid_arguments": 422, "forbidden_argument": 422, "validation_failed": 422}.get(res.error_code, 400)
        raise HTTPException(code, {"code": res.error_code, "message": res.error})
    db.commit()
    return res.data


class DecisionIn(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    comment: str | None = Field(None, max_length=1000)


@router.post("/actions/{proposal_id}/approve")
def approve(proposal_id: str, body: DecisionIn, ctx: TenantContext = Depends(current_tenant),
            db: Session = Depends(get_db)):
    svc = ActionService(db, ctx)
    p = svc.approve(proposal_id, payload_hash_seen=body.payload_hash, comment=body.comment)
    return _p(p, svc.history(proposal_id))


@router.post("/actions/{proposal_id}/reject")
def reject(proposal_id: str, body: DecisionIn, ctx: TenantContext = Depends(current_tenant),
           db: Session = Depends(get_db)):
    svc = ActionService(db, ctx)
    p = svc.reject(proposal_id, payload_hash_seen=body.payload_hash, comment=body.comment)
    return _p(p, svc.history(proposal_id))


# ---- audit -----------------------------------------------------------------------------------
@router.get("/audit")
def audit(limit: int = Query(100, ge=1, le=500), ctx: TenantContext = Depends(current_tenant),
          db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.company_id == ctx.company_id)
                      .order_by(AuditLog.id.desc()).limit(limit))
    return [{"id": a.id, "ts": a.ts, "actor_id": a.actor_id, "actor_type": a.actor_type, "event_type": a.event_type,
             "action_proposal_id": a.action_proposal_id, "device_id": a.device_id, "details": a.details,
             "entry_hash": a.entry_hash} for a in rows]


@router.get("/audit/verify")
def audit_verify(ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    ok, bad = verify_chain(db, ctx.company_id)
    return {"chain_valid": ok, "first_invalid_id": bad}
