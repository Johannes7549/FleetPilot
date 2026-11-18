"""LangGraph state. Only observable execution data lives here — plans, tool calls, results, evidence
and decisions. No hidden model reasoning is requested, stored or displayed."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from sqlalchemy.orm import Session

from app.agents.llm.providers import GuardedLLM
from app.core.config import Settings
from app.security.context import TenantContext


class AgentState(TypedDict, total=False):
    run_id: str
    question: str
    history: list[dict]
    guard: dict
    intent: str
    intent_confidence: float
    entities: dict
    plan: list[dict]
    plan_source: str  # llm | deterministic | deterministic_fallback | llm_review | llm_repair
    intent_source: str  # deterministic | llm
    clarification: str | None
    context: dict  # entities carried over from earlier turns (multi-turn follow-ups)
    followup_steps: list[dict]  # read steps requested by the LLM result review (bounded)
    replan_sources: list[str]
    cursor: int
    replans: int
    tool_results: Annotated[list[dict], operator.add]
    validation: dict
    evidence: list[dict]
    proposals: list[dict]
    refused_actions: list[dict]
    response: dict
    timeline: Annotated[list[dict], operator.add]
    token_usage: Annotated[list[dict], operator.add]


@dataclass
class AgentDeps:
    session: Session
    tenant: TenantContext
    settings: Settings
    llm: GuardedLLM | None  # None = deterministic mode
    company_name: str
    other_companies: dict[str, str]
    known_software: list[str]
    tool_ctx: Any = None  # ToolContext shared by every step of one run (one FleetAnalysis per run)


def deps_of(config: Any) -> AgentDeps:
    return config["configurable"]["deps"]
