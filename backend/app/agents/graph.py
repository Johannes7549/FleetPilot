"""FleetPilot LangGraph workflow.

    START -> guard ─blocked──────────────────────────────────────────────┐
               └─> classify ─help/out_of_scope/unsupported──────────────┤
                       └─> plan ─(no steps)───────────────────────────────┤
                             └─> execute ⟲ (one tool per iteration,      │
                                  │        bounded by agent_max_tool_calls)
                                  └─> validate (+ LLM result review) ─replan─> plan (max agent_max_replans)
                                         ├─action_request & evidence──> propose ─┐
                                         └────────────────────────────────────> respond -> END
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.act import propose_actions
from app.agents.nodes.execute import execute_step
from app.agents.nodes.guard import guard_input
from app.agents.nodes.respond import respond
from app.agents.nodes.understand import classify, make_plan
from app.agents.nodes.validate import validate_evidence
from app.agents.state import AgentState, deps_of


def after_guard(state: AgentState) -> str:
    return "respond" if state["guard"]["blocked"] else "classify"


def after_classify(state: AgentState, config) -> str:
    intent = state["intent"]
    llm = deps_of(config).llm
    # With an LLM available, requests the rule-based classifier could not place are interpreted by the LLM
    # planner instead of being rejected (unusual phrasings). Unsupported-data stays deterministic.
    if intent == "out_of_scope" and llm is not None and llm.enabled("plan"):
        return "plan"
    return "respond" if intent in ("help", "out_of_scope", "unsupported_data") else "plan"


def after_plan(state: AgentState) -> str:
    return "execute" if state.get("cursor", 0) < len(state.get("plan") or []) else "validate"


def after_execute(state: AgentState, config) -> str:
    deps = deps_of(config)
    executed = len([r for r in state.get("tool_results") or [] if r.get("kind") != "action"])
    if state["cursor"] < len(state["plan"]) and executed < deps.settings.agent_max_tool_calls:
        return "execute"
    return "validate"


def after_validate(state: AgentState) -> str:
    status = state["validation"]["status"]
    if status == "replan":
        return "plan"
    if state.get("intent") == "action_request" and status in ("sufficient", "partial"):
        return "propose"
    return "respond"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("guard", guard_input)
    g.add_node("classify", classify)
    g.add_node("plan", make_plan)
    g.add_node("execute", execute_step)
    g.add_node("validate", validate_evidence)
    g.add_node("propose", propose_actions)
    g.add_node("respond", respond)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", after_guard, {"respond": "respond", "classify": "classify"})
    g.add_conditional_edges("classify", after_classify, {"respond": "respond", "plan": "plan"})
    g.add_conditional_edges("plan", after_plan, {"execute": "execute", "validate": "validate"})
    g.add_conditional_edges("execute", after_execute, {"execute": "execute", "validate": "validate"})
    g.add_conditional_edges("validate", after_validate, {"plan": "plan", "propose": "propose", "respond": "respond"})
    g.add_edge("propose", "respond")
    g.add_edge("respond", END)
    return g.compile()


@lru_cache
def get_graph():
    return build_graph()
