from __future__ import annotations

import time

from app.agents.state import AgentState, deps_of
from app.agents.timeline import event
from app.guardrails.input_guard import screen_question


def guard_input(state: AgentState, config) -> dict:
    t0 = time.perf_counter()
    deps = deps_of(config)
    res = screen_question(state["question"], own_company_id=deps.tenant.company_id,
                          own_company_name=deps.company_name, other_companies=deps.other_companies)
    guard = {"blocked": res.blocked, "reason": res.reason, "code": res.code, "flags": res.flags}
    return {
        "guard": guard,
        "timeline": [
            event("request_received", "Request received", input={"question": state["question"],
                                                                 "user": deps.tenant.user_id, "role": deps.tenant.role}),
            event("guardrail", "Input guard", status="blocked" if res.blocked else "ok", output=guard, started=t0),
        ],
    }
