"""Tool execution node — runs ONE plan step per invocation. The graph loops back here while steps
remain and the tool-call budget is not exhausted (explicit, bounded agent loop)."""

from __future__ import annotations

import json

from app.agents.state import AgentState, deps_of
from app.agents.timeline import event
from app.tools import registry


def execute_step(state: AgentState, config) -> dict:
    deps = deps_of(config)
    i = state.get("cursor", 0)
    step = state["plan"][i]
    ctx = deps.tool_ctx
    res = registry.execute(step["tool"], step["args"], ctx)
    spec = registry.get(step["tool"])
    out = res.model_dump(mode="json")
    out["kind"] = spec.kind if spec else "unknown"
    ev_ids = [e.evidence_id for e in res.evidence]
    return {
        "cursor": i + 1,
        "tool_results": [out],
        "timeline": [event("tool_call", step["tool"], status="ok" if res.ok else "error",
                           input={"args": step["args"], "purpose": step.get("purpose")},
                           output={"summary": res.summary, "error": res.error, "error_code": res.error_code,
                                   "attempts": res.attempts, "evidence_count": len(ev_ids),
                                   # bounded preview of the structured result, for the execution timeline
                                   "result_preview": json.dumps(res.data, default=str)[:1500] if res.ok else None},
                           evidence_ids=ev_ids[:200], duration_ms=res.duration_ms)],
    }

