"""Response generation node: deterministic answer by default; optional LLM phrasing that must pass
the grounding verifier or is discarded."""

from __future__ import annotations

import json
import time

from app.agents.composer import compact_results, deterministic_answer, tool_payload_text, verify_grounding
from app.agents.prompts import load
from app.agents.state import AgentState, deps_of
from app.agents.timeline import event
from app.agents.llm.providers import LLMCallError
from app.guardrails.injection import sanitize_payload, wrap_untrusted

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "cited_evidence_ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["answer", "cited_evidence_ids"],
    "additionalProperties": False,
}


def _status(state: AgentState) -> str:
    if (state.get("guard") or {}).get("blocked"):
        return "refused"
    if state.get("clarification") and not state.get("tool_results"):
        return "needs_clarification"
    intent = state.get("intent")
    if intent in ("out_of_scope", "unsupported_data"):
        return "cannot_answer"
    if intent == "help":
        return "answered"
    v = (state.get("validation") or {}).get("status")
    if v == "not_found":
        return "not_found"
    if v in ("insufficient", "replan"):
        return "insufficient_evidence"
    if intent == "action_request":
        return "awaiting_approval" if state.get("proposals") else "action_refused"
    return "partial" if v == "partial" else "answered"


def respond(state: AgentState, config) -> dict:
    t0 = time.perf_counter()
    deps = deps_of(config)
    answer = deterministic_answer(state)
    method = "deterministic"
    events, usage, grounding_issues, flags = [], [], [], []
    evidence = state.get("evidence") or []
    status = _status(state)

    composable = status in ("answered", "partial", "awaiting_approval", "action_refused")
    if deps.llm is not None and composable and "respond" in deps.llm.stages and not deps.llm.enabled("respond"):
        # Circuit open after earlier failures in this run: skip the call, say so.
        method = "deterministic_after_llm_error"
        events.append(event("llm_call", "LLM response composer", status="skipped",
                            output={"error_kind": "disabled", "error": deps.llm.disabled_reason,
                                    "fallback": "deterministic answer"}))
    elif deps.llm is not None and deps.llm.enabled("respond") and composable:
        t1 = time.perf_counter()
        results = state.get("tool_results") or []
        payload = tool_payload_text(results)
        # Untrusted telemetry strings are sanitised before the model sees them; the same truncation
        # budget is applied to what the model sees and to what the verifier accepts.
        safe = json.dumps(sanitize_payload(json.loads(json.dumps(compact_results(results), default=str)), flags))[:24000]
        ev_ids = {e["evidence_id"] for e in evidence}
        user = (f"Question: {state['question']}\n\nProposals created: {json.dumps(state.get('proposals') or [], default=str)[:4000]}\n"
                f"Refused actions: {json.dumps(state.get('refused_actions') or [], default=str)[:2000]}\n"
                f"Evidence IDs available: {sorted(ev_ids)[:300]}\n\n" + wrap_untrusted("tool_results", safe))
        try:
            res = deps.llm.complete_json(stage="respond", system=load("composer"), user=user, schema=ANSWER_SCHEMA, max_tokens=3000)
            usage.append({"stage": "respond", **res.usage, "model": res.model})
            cand = str(res.data.get("answer", "")).strip()
            cited = [str(c) for c in res.data.get("cited_evidence_ids", [])]
            grounding_issues = verify_grounding(cand, cited, payload + json.dumps(state.get("proposals") or [], default=str), ev_ids)
            if cand and not grounding_issues:
                answer, method = cand, "llm_verified"
            else:
                method = "deterministic_after_grounding_failure"
            events.append(event("llm_call", "LLM response composer", status="ok" if method == "llm_verified" else "rejected",
                                input={"stage": "respond", "evidence_ids_offered": len(ev_ids)},
                                output={"grounding_issues": grounding_issues[:20], "injection_flags": flags[:20],
                                        "cited_evidence_ids": cited[:50], "accepted": method == "llm_verified"},
                                started=t1, token_usage=res.usage, model=res.model))
        except LLMCallError as exc:
            method = "deterministic_after_llm_error"
            events.append(event("llm_call", "LLM response composer", status="error",
                                output={"error_kind": exc.kind, "error": exc.message, "fallback": "deterministic answer"},
                                started=t1))

    response = {
        "answer": answer,
        "status": status,
        "intent": state.get("intent"),
        "evidence": evidence,
        "proposals": state.get("proposals") or [],
        "refused_actions": state.get("refused_actions") or [],
        "tools_used": [{"tool": r["tool"], "args": r["args"], "ok": r["ok"], "error": r.get("error"),
                        "error_code": r.get("error_code"), "duration_ms": r.get("duration_ms"), "kind": r.get("kind")}
                       for r in state.get("tool_results") or []],
        "grounding": {"method": method, "issues": grounding_issues, "plan_source": state.get("plan_source"),
                      "replan_sources": state.get("replan_sources") or [], "validation": state.get("validation")},
        "plan": state.get("plan") or [],
        "intent_source": state.get("intent_source", "deterministic"),
        "clarification": state.get("clarification"),
        "context": state.get("context") or None,
        "guard": state.get("guard"),
    }
    events.append(event("response", "Response generated", output={"status": status, "method": method,
                                                                  "evidence_count": len(evidence)},
                        evidence_ids=[e["evidence_id"] for e in evidence][:200], started=t0))
    return {"response": response, "timeline": events, "token_usage": usage}
