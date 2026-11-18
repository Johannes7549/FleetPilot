"""Intent classification and planning nodes.

classify  - deterministic intent + entity extraction (always runs; authoritative for safety-relevant intents)
            plus multi-turn context carry-over ("what about its battery?").
make_plan - initial plan: LLM planner when enabled (validated), deterministic planner otherwise / on failure.
            re-plan:     appends LLM-requested follow-up steps (from the result review), or repairs failed
                         steps (LLM repair, else deterministic) — never re-executes successful steps.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict

from app.agents import planning
from app.agents.llm.providers import LLMCallError
from app.agents.prompts import load
from app.agents.state import AgentState, deps_of
from app.agents.timeline import event
from app.guardrails.injection import wrap_untrusted
from app.tools import registry

MAX_PLAN_STEPS = 6
DATA_INTENTS = ("fleet_query", "insight_request", "device_lookup", "action_request")
ACTION_TYPES = ("create_upgrade_order", "open_remediation_ticket", "flag_device_for_replacement", "notify_employee")
CHECK_IDS = ("screen_lock", "disk_encryption", "os_up_to_date")
TENANT_ARGS = ("company_id", "tenant_id", "company", "org_id", "organization_id")

STEP_SCHEMA = {
    "type": "object",
    "properties": {"tool": {"type": "string"}, "args_json": {"type": "string"}, "purpose": {"type": "string"}},
    "required": ["tool", "args_json", "purpose"], "additionalProperties": False,
}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(planning.INTENTS)},
        "steps": {"type": "array", "items": STEP_SCHEMA},
        "clarification": {"type": "string"},
        "action": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["none", *ACTION_TYPES]},
                "component": {"type": "string", "enum": ["none", "memory", "storage", "battery"]},
                "check_id": {"type": "string"},
                "target_device_ids": {"type": "array", "items": {"type": "string"}},
                "target_employee_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["action_type", "component", "check_id", "target_device_ids", "target_employee_ids"],
            "additionalProperties": False,
        },
    },
    "required": ["intent", "steps", "clarification", "action"],
    "additionalProperties": False,
}
NO_ACTION = {"action_type": "none", "component": "none", "check_id": "", "target_device_ids": [],
             "target_employee_ids": []}

ANAPHORA_RE = re.compile(
    r"\b(it|its|it's|that device|this device|those devices|these devices|the same device|that laptop|"
    r"that machine|them|their|those|these|that one|the owner|the owners)\b|^\s*(and|what about|how about)\b",
    re.IGNORECASE)


def _previous_targets(history: list[dict]) -> tuple[list[str], list[str]]:
    for h in reversed(history or []):
        if h.get("role") == "assistant" and (h.get("devices") or h.get("employees")):
            return list(h.get("devices") or []), list(h.get("employees") or [])
    return [], []


def classify(state: AgentState, config) -> dict:
    t0 = time.perf_counter()
    deps = deps_of(config)
    q = state["question"]
    ents = planning.extract_entities(q, deps.known_software)
    context: dict = {}
    # Multi-turn: a follow-up that refers back ("its battery", "notify their owners") inherits the targets of
    # the previous answer — deterministically, so it works without an LLM. Bounded to small target sets.
    if not ents.device_ids and not ents.employee_ids and ANAPHORA_RE.search(q):
        devs, emps = _previous_targets(state.get("history") or [])
        if 0 < len(devs) <= 5:
            ents.device_ids = devs
            context = {"carried_device_ids": devs, "reason": "follow-up refers to the previous answer"}
        elif 0 < len(emps) <= 5:
            ents.employee_ids = emps
            context = {"carried_employee_ids": emps, "reason": "follow-up refers to the previous answer"}
    intent, conf = planning.classify_intent(q, ents)
    out = {"intent": intent, "intent_confidence": conf, "entities": asdict(ents), "context": context}
    events = [event("intent", "Intent identified (deterministic)", output={
        "intent": intent, "confidence": conf, "entities": asdict(ents), "history_turns": len(state.get("history") or []),
        "context_carryover": context or None}, started=t0)]
    return out | {"intent_source": "deterministic", "timeline": events}


# --------------------------------------------------------------------------------------------------
def validate_llm_steps(raw_steps: list[dict], *, limit: int = MAX_PLAN_STEPS) -> tuple[list[dict], list[str]]:
    """LLM output is untrusted: keep only known *read* tools, strip tenant args, validate args."""
    steps, issues = [], []
    for s in raw_steps[:limit]:
        spec = registry.get(s.get("tool", ""))
        if spec is None:
            issues.append(f"unknown tool {s.get('tool')!r} dropped")
            continue
        if spec.kind != "read":
            issues.append(f"action tool {spec.name} dropped from plan (actions are proposed by the system)")
            continue
        try:
            args = json.loads(s.get("args_json") or "{}")
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
        except ValueError as exc:
            issues.append(f"{spec.name}: unparseable args ({exc})")
            continue
        for k in TENANT_ARGS:
            if args.pop(k, None) is not None:
                issues.append(f"{spec.name}: removed tenant argument '{k}' supplied by the model")
        try:
            spec.input_model.model_validate(args)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{spec.name}: invalid args ({str(exc).splitlines()[0]})")
            continue
        step = {"tool": spec.name, "args": args, "purpose": str(s.get("purpose", ""))[:200]}
        if any(x["tool"] == step["tool"] and x["args"] == step["args"] for x in steps):
            issues.append(f"{spec.name}: duplicate step dropped")
            continue
        steps.append(step)
    if len(raw_steps) > limit:
        issues.append(f"{len(raw_steps) - limit} step(s) beyond the {limit}-step budget dropped")
    return steps, issues


def _merge_action_hints(ents: planning.Entities, hint: dict) -> dict:
    """Fills gaps in the deterministic action entities from the LLM's structured hints. Deterministic values
    win; every hint is validated (enum / ID format). Targets still go through tenant + evidence checks."""
    applied: dict = {}
    if not ents.action_type and hint.get("action_type") in ACTION_TYPES:
        ents.action_type = applied["action_type"] = hint["action_type"]
    if not ents.component and hint.get("component") in ("memory", "storage", "battery"):
        ents.component = applied["component"] = hint["component"]
    if not ents.check_id and hint.get("check_id") in CHECK_IDS:
        ents.check_id = applied["check_id"] = hint["check_id"]
    if not ents.device_ids and not ents.employee_ids:
        devs = [d for d in hint.get("target_device_ids") or [] if planning.DEVICE_ID_RE.fullmatch(d)][:10]
        emps = [e.lower() for e in hint.get("target_employee_ids") or [] if planning.EMPLOYEE_ID_RE.fullmatch(e)][:10]
        if devs:
            ents.device_ids = applied["device_ids"] = devs
        if emps:
            ents.employee_ids = applied["employee_ids"] = emps
    return applied


def _history_text(history: list[dict]) -> str:
    lines = []
    for h in (history or [])[-6:]:
        extra = ""
        if h.get("devices"):
            extra += f" [devices referenced: {', '.join(h['devices'][:10])}]"
        if h.get("employees"):
            extra += f" [employees referenced: {', '.join(h['employees'][:10])}]"
        if h.get("tools"):
            extra += f" [tools used: {', '.join(h['tools'][:6])}]"
        lines.append(f"{h['role']}: {str(h.get('content', ''))[:400]}{extra}")
    return "\n".join(lines)


def _catalog() -> str:
    return json.dumps([{k: t[k] for k in ("name", "description", "input_schema")} for t in registry.catalog("read")])


def _llm_plan(state: AgentState, deps, ents: planning.Entities, t0: float) -> tuple[dict | None, list[dict], list[dict]]:
    """Returns (decision, events, usage). decision is None when the LLM stage failed or is disabled."""
    events, usage = [], []
    user = (f"Recent conversation (context only, oldest first):\n{_history_text(state.get('history') or []) or '(none)'}\n\n"
            f"Deterministic pre-classification (hint, may be wrong): intent={state['intent']}\n\n"
            + wrap_untrusted("request", state["question"]))
    try:
        res = deps.llm.complete_json(stage="plan", system=load("planner").replace("{catalog}", _catalog()),
                                     user=user, schema=PLAN_SCHEMA, max_tokens=2000)
    except LLMCallError as exc:
        events.append(event("llm_call", "LLM planner", status="error", started=t0,
                            input={"stage": "plan", "provider": deps.llm.name, "model": deps.llm.model},
                            output={"error_kind": exc.kind, "error": exc.message, "fallback": "deterministic planner"}))
        return None, events, usage
    usage.append({"stage": "plan", **res.usage, "model": res.model})
    data = res.data
    steps, issues = validate_llm_steps(data.get("steps") or [])
    det_intent, llm_intent = state["intent"], data.get("intent")
    hint = data.get("action") or NO_ACTION
    applied: dict = {}
    # Deterministic classifier stays authoritative for safety-relevant intents: an action request is never
    # downgraded, unsupported-data questions are never "answered" from unrelated tools.
    if det_intent in ("action_request", "unsupported_data"):
        intent = det_intent
    elif llm_intent == "action_request":
        intent = "action_request" if (ents.action_type or hint.get("action_type") in ACTION_TYPES) else det_intent
        if intent != "action_request":
            issues.append("LLM intent action_request without a valid action type ignored")
    else:
        intent = llm_intent
    if intent == "action_request":
        applied = _merge_action_hints(ents, hint)
    clarification = (data.get("clarification") or "").strip()
    events.append(event("llm_call", "LLM planner", started=t0, token_usage=res.usage, model=res.model,
                        input={"stage": "plan", "provider": deps.llm.name, "model": res.model,
                               "history_turns": len(state.get("history") or [])},
                        output={"intent": llm_intent, "intent_used": intent, "validation_issues": issues,
                                "raw_steps": data.get("steps"), "action_hints_applied": applied or None,
                                "clarification": clarification or None}))
    return {"intent": intent, "steps": steps, "clarification": clarification, "issues": issues}, events, usage


def _executed(state: AgentState) -> list[tuple[str, str]]:
    return [(r["tool"], json.dumps(r.get("args") or {}, sort_keys=True)) for r in state.get("tool_results") or []
            if r.get("ok") and r.get("kind") == "read"]


def make_plan(state: AgentState, config) -> dict:
    t0 = time.perf_counter()
    deps = deps_of(config)
    ents = planning.Entities(**state["entities"])
    if state.get("replans", 0) > 0:
        return _replan(state, deps, ents, t0)

    intent = state["intent"]
    events, usage = [], []
    source = "deterministic"
    steps: list[dict] = []
    clarification = None
    intent_source = "deterministic"

    if deps.llm is not None and deps.llm.enabled("plan") and intent != "help":
        decision, events, usage = _llm_plan(state, deps, ents, t0)
        if decision is not None:
            intent, steps, source, intent_source = decision["intent"], decision["steps"], "llm", "llm"
            if intent in ("help", "out_of_scope", "unsupported_data"):
                steps = []
            elif not steps:
                if decision["clarification"] and intent != "action_request":
                    clarification = decision["clarification"][:500]
                elif intent != "action_request":
                    source = "deterministic_fallback"  # LLM produced no usable step; use the rule planner
        else:
            source = "deterministic_fallback"

    if source in ("deterministic", "deterministic_fallback"):
        if intent == "action_request":
            steps = [s.as_dict() for s in planning.action_gathering_plan(ents)]
        else:
            steps = [s.as_dict() for s in planning.plan(state["question"], intent, ents)]
    elif intent == "action_request" and not steps:
        steps = [s.as_dict() for s in planning.action_gathering_plan(ents)]

    steps = steps[:MAX_PLAN_STEPS]
    events.append(event("plan", "Execution plan", output={
        "source": source, "intent": intent, "intent_source": intent_source, "steps": steps,
        "clarification": clarification}, started=t0))
    if steps:
        events.append(event("tool_selection", "Tools selected",
                            output={"tools": [s["tool"] for s in steps], "args": [s["args"] for s in steps]}))
    return {"plan": steps, "plan_source": source, "cursor": 0, "intent": intent, "intent_source": intent_source,
            "entities": asdict(ents), "clarification": clarification, "timeline": events, "token_usage": usage}


def _replan(state: AgentState, deps, ents: planning.Entities, t0: float) -> dict:
    """Bounded re-plan. Appends steps after the already-executed plan (cursor continues from there)."""
    old = list(state.get("plan") or [])
    done = set(_executed(state))
    events, usage = [], []
    followups = state.get("followup_steps") or []
    if followups:
        new, source = followups, "llm_review"
    else:
        failed = [r for r in state.get("tool_results") or [] if r.get("kind") == "read" and not r["ok"]]
        new, source = [], "deterministic_fallback"
        if deps.llm is not None and deps.llm.enabled("plan"):
            errors = [{"tool": r["tool"], "args": r["args"], "error_code": r.get("error_code"), "error": r.get("error")}
                      for r in failed]
            user = (f"Some tool calls failed. Produce corrected read steps only for what is still needed.\n"
                    f"Failed calls: {json.dumps(errors, default=str)[:3000]}\n\n" + wrap_untrusted("request", state["question"]))
            try:
                res = deps.llm.complete_json(stage="plan", system=load("planner").replace("{catalog}", _catalog()),
                                             user=user, schema=PLAN_SCHEMA, max_tokens=1500)
                usage.append({"stage": "plan_repair", **res.usage, "model": res.model})
                new, issues = validate_llm_steps(res.data.get("steps") or [])
                source = "llm_repair"
                events.append(event("llm_call", "LLM plan repair", started=t0, token_usage=res.usage, model=res.model,
                                    input={"stage": "plan_repair", "failed_calls": errors},
                                    output={"steps": new, "validation_issues": issues}))
            except LLMCallError as exc:
                events.append(event("llm_call", "LLM plan repair", status="error", started=t0,
                                    output={"error_kind": exc.kind, "error": exc.message}))
        if source != "llm_repair":
            base = (planning.action_gathering_plan(ents) if state["intent"] == "action_request"
                    else planning.plan(state["question"], state["intent"], ents))
            new = [s.as_dict() for s in base]
    budget = max(0, deps.settings.agent_max_tool_calls - len(done))
    fresh = [s for s in new if (s["tool"], json.dumps(s["args"], sort_keys=True)) not in done]
    fresh = [s for i, s in enumerate(fresh) if s not in fresh[:i]][:budget]
    plan = old + fresh
    events.append(event("plan", f"Re-plan #{state.get('replans', 0)}", output={
        "source": source, "added_steps": fresh, "reason": (state.get("validation") or {}).get("reason")}, started=t0))
    return {"plan": plan, "cursor": len(old), "replan_sources": (state.get("replan_sources") or []) + [source],
            "followup_steps": [], "timeline": events, "token_usage": usage}
