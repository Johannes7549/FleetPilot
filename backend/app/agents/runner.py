"""Runs the graph for one request and persists the run + observable timeline."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.agents.graph import get_graph
from app.agents.llm.providers import GuardedLLM, LLMProvider, get_provider, llm_status
from app.agents.state import AgentDeps
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import AgentRun, AgentStep, Company, InstalledSoftware
from app.security.context import TenantContext
from app.services.audit import append_audit
from app.tools import ToolContext

logger = get_logger(__name__)
_UNSET = object()


def run_agent(session: Session, tenant: TenantContext, question: str, *, conversation_id: str | None = None,
              history: list[dict] | None = None, settings: Settings | None = None,
              llm: LLMProvider | None | object = _UNSET) -> dict:
    settings = settings or get_settings()
    if llm is _UNSET:
        provider = get_provider(settings)
        status_info = llm_status(settings)
    else:  # injected provider (tests / evaluations)
        provider = llm
        status_info = {"configured_provider": getattr(llm, "name", "deterministic") if llm else "deterministic",
                       "unavailable_reason": None, "model": getattr(llm, "model", None)}
    stages = {"plan", "review"} | ({"respond"} if settings.llm_stages == "all" else set())
    guarded = provider if isinstance(provider, GuardedLLM) or provider is None else GuardedLLM(provider, stages=stages)
    provider = guarded
    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    companies = {c.id: c.name for c in session.scalars(select(Company))}
    own_name = companies.get(tenant.company_id, tenant.company_id)
    known_sw = list(session.scalars(select(distinct(InstalledSoftware.name))
                                    .where(InstalledSoftware.company_id == tenant.company_id)))
    deps = AgentDeps(session=session, tenant=tenant, settings=settings, llm=provider, company_name=own_name,
                     other_companies={k: v for k, v in companies.items() if k != tenant.company_id},
                     known_software=known_sw)
    deps.tool_ctx = ToolContext(session=session, tenant=tenant, settings=settings, agent_run_id=run_id)

    run = AgentRun(id=run_id, company_id=tenant.company_id, user_id=tenant.user_id, conversation_id=conversation_id,
                   question=question[:4000], status="running", provider=getattr(provider, "name", "deterministic"))
    session.add(run)
    session.flush()

    state: dict = {"run_id": run_id, "question": question, "history": history or [], "replans": 0,
                   "tool_results": [], "timeline": [], "token_usage": []}
    try:
        final = get_graph().invoke(state, config={"configurable": {"deps": deps}, "recursion_limit": 40})
        response = final["response"]
        status = response["status"]
        error = None
    except Exception as exc:  # noqa: BLE001 - never surface a stack trace to the user
        logger.exception("agent run failed", extra={"ctx": {"run_id": run_id}})
        session.rollback()
        session.add(run)
        final = {"timeline": state["timeline"], "token_usage": []}
        response = {"answer": "The request failed due to an internal error. No actions were taken.",
                    "status": "error", "intent": None, "evidence": [], "proposals": [], "refused_actions": [],
                    "tools_used": [], "grounding": {"method": "none"}, "guard": None}
        status, error = "error", f"{exc.__class__.__name__}: {exc}"

    duration = int((time.perf_counter() - t0) * 1000)
    execution = _execution_summary(guarded, status_info, final, response, settings)
    usage = final.get("token_usage") or []
    totals = {"input_tokens": sum(u.get("input_tokens", 0) for u in usage),
              "output_tokens": sum(u.get("output_tokens", 0) for u in usage), "calls": usage}
    run.intent = response.get("intent")
    run.status = status
    run.plan = final.get("plan")
    run.response = {k: v for k, v in response.items()} | {"execution": execution}
    run.token_usage = totals
    run.error = error
    run.duration_ms = duration
    for i, ev in enumerate(final.get("timeline") or []):
        session.add(AgentStep(run_id=run_id, seq=i, step_type=ev["step_type"], name=ev["name"][:128],
                              status=ev["status"], input=ev.get("input"),
                              output={k: v for k, v in ev.items() if k in ("output", "token_usage", "model")},
                              evidence_ids=ev.get("evidence_ids"), duration_ms=ev.get("duration_ms"),
                              started_at=datetime.fromisoformat(ev["started_at"])))
    if response.get("guard") and response["guard"].get("blocked"):
        append_audit(session, company_id=tenant.company_id, actor_id=tenant.user_id, actor_type="user",
                     event_type="agent.request_blocked", details={"run_id": run_id, "code": response["guard"]["code"],
                                                                  "question": question[:500]})
    session.commit()
    response = dict(response)
    response.update(run_id=run_id, duration_ms=duration, token_usage=totals, provider=run.provider,
                    execution=execution, created_at=datetime.now(UTC).isoformat())
    return response


def _execution_summary(guarded: GuardedLLM | None, status_info: dict, final: dict, response: dict,
                       settings: Settings) -> dict:
    """Per-run statement of how the answer was produced. Never claims LLM execution that did not happen.

    mode: deterministic           - no LLM configured
          deterministic_fallback  - an LLM was selected but unusable, or every LLM call failed
          llm                     - LLM planned / reviewed / composed and every LLM stage succeeded
          hybrid                  - LLM plans and reviews, answers composed deterministically (LLM_STAGES=plan),
                                    or some LLM stages fell back to the deterministic path (see `fallbacks`)
    """
    calls = list(guarded.calls) if guarded else []
    fallbacks: list[dict] = []
    for c in calls:
        if not c.get("ok"):
            fallbacks.append({"stage": c["stage"], "reason": f"{c.get('error_kind')}: {c.get('error')}"})
    method = (response.get("grounding") or {}).get("method")
    if method == "deterministic_after_grounding_failure":
        fallbacks.append({"stage": "respond", "reason": "LLM answer failed grounding verification; deterministic answer used"})
    if final.get("plan_source") == "deterministic_fallback" and not any(f["stage"] == "plan" for f in fallbacks):
        fallbacks.append({"stage": "plan", "reason": "LLM plan had no usable steps; deterministic planner used"})
    ok_calls = [c for c in calls if c.get("ok")]
    if guarded is None:
        mode = "deterministic_fallback" if status_info.get("unavailable_reason") else "deterministic"
    elif calls and not ok_calls:
        mode = "deterministic_fallback"
    elif not calls:
        mode = "deterministic"  # nothing needed an LLM (e.g. blocked by the input guard, help)
    else:
        mode = "llm" if (not fallbacks and settings.llm_stages == "all") else "hybrid"
    warning = None
    if status_info.get("unavailable_reason"):
        warning = (f"LLM provider '{status_info['configured_provider']}' selected but "
                   f"{status_info['unavailable_reason']}: answered in deterministic fallback mode.")
    elif fallbacks:
        warning = "Some LLM stages failed or were rejected and fell back to deterministic execution."
    return {
        "mode": mode,
        "configured_provider": status_info.get("configured_provider"),
        "provider": guarded.name if guarded else None,
        "model": (ok_calls[0].get("model") if ok_calls else (guarded.model if guarded else None)),
        "llm_stages_enabled": sorted(guarded.stages) if guarded else [],
        "llm_calls": calls,
        "fallbacks": fallbacks,
        "circuit_open": guarded.disabled_reason if guarded else None,
        "warning": warning,
        "intent_source": response.get("intent_source"),
        "plan_source": (response.get("grounding") or {}).get("plan_source"),
        "answer_source": method,
    }
