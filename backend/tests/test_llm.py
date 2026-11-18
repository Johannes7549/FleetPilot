"""LLM integration: provider status/mode reporting, SDK-level error mapping and retries, structured output,
validated planning, multi-step follow-ups, multi-turn context and deterministic fallback.

All provider calls go through the real Anthropic/OpenAI SDK clients over a scripted HTTP transport
(`app.evaluation.llm_mock`) — no API key, no network.
"""

from __future__ import annotations

import json

import pytest

from app.agents.llm.providers import GuardedLLM, LLMCallError, clear_provider_error, get_provider, llm_status
from app.agents.nodes.understand import PLAN_SCHEMA
from app.agents.runner import run_agent
from app.core.config import Settings
from app.evaluation.llm_mock import mock_provider, plan, review
from tests.conftest import ACME_OPERATOR


def S(**kw) -> Settings:
    base = {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""}
    return Settings(**(base | kw))


def tools(r):
    return [t["tool"] for t in r["tools_used"]]


# ---- status / mode reporting ----------------------------------------------------------------------
def test_status_deterministic():
    st = llm_status(S(llm_provider="deterministic"))
    assert st["active_mode"] == "deterministic" and st["warning"] is None and st["provider"] is None


def test_selected_provider_without_key_reports_fallback_not_llm():
    clear_provider_error()
    st = llm_status(S(llm_provider="anthropic"))
    assert st["active_mode"] == "deterministic_fallback"
    assert "ANTHROPIC_API_KEY" in st["warning"] and st["api_key_configured"] is False
    assert get_provider(S(llm_provider="anthropic")) is None


def test_status_with_key_and_stage_modes():
    clear_provider_error()
    assert llm_status(S(llm_provider="anthropic", ANTHROPIC_API_KEY="x"))["active_mode"] == "llm"
    hy = llm_status(S(llm_provider="openai", OPENAI_API_KEY="sk-test-SECRETVALUE", llm_stages="plan"))
    assert hy["active_mode"] == "hybrid" and hy["llm_stages"] == ["plan", "review"] and hy["model"] == "gpt-4o-mini"
    assert "SECRETVALUE" not in json.dumps(hy)  # never echoes secrets


def test_run_reports_deterministic_fallback_when_key_missing(session):
    r = run_agent(session, ACME_OPERATOR, "Which devices are low on disk space?", settings=S(llm_provider="anthropic"))
    ex = r["execution"]
    assert ex["mode"] == "deterministic_fallback" and ex["llm_calls"] == [] and "ANTHROPIC_API_KEY" in ex["warning"]
    assert r["status"] == "answered"


# ---- provider error mapping through the real SDKs ---------------------------------------------------
@pytest.mark.parametrize("item,kind", [
    (("http", 401), "auth"), (("http", 400), "api_error"), (("text", "not json", "end_turn"), "invalid_json"),
    (("text", '{"intent": "x"', "max_tokens"), "truncated"), (("refusal",), "refusal"), (("timeout",), "timeout"),
    ({"intent": "fleet_query"}, "schema_violation"),
])
def test_anthropic_error_mapping(item, kind):
    prov, _ = mock_provider({"plan": [item] * 3}, max_retries=0)
    with pytest.raises(LLMCallError) as e:
        prov.complete_json(system="s", user="u", schema=PLAN_SCHEMA)
    assert e.value.kind == kind


def test_rate_limit_retried_by_sdk_then_exhausted():
    prov, be = mock_provider({"plan": [("http", 429), plan("fleet_query", [])]}, max_retries=2)
    assert prov.complete_json(system="s", user="u", schema=PLAN_SCHEMA).data["intent"] == "fleet_query"
    assert len(be.requests) == 2
    prov, be = mock_provider({"plan": [("http", 429)] * 5}, max_retries=1)
    with pytest.raises(LLMCallError) as e:
        prov.complete_json(system="s", user="u", schema=PLAN_SCHEMA)
    assert e.value.kind == "rate_limit" and len(be.requests) == 2


def test_openai_structured_output_request_shape():
    prov, be = mock_provider({"plan": [plan("fleet_query", [("find_low_storage_devices", {})])]}, kind="openai")
    prov.complete_json(system="sys", user="u", schema=PLAN_SCHEMA, max_tokens=100)
    body = be.requests[0]["body"]
    assert body["response_format"]["json_schema"]["strict"] is True and body["max_completion_tokens"] == 100


def test_anthropic_request_uses_structured_output_and_no_tenant_id(session):
    prov, be = mock_provider({"plan": [plan("fleet_query", [("find_low_storage_devices", {})])]})
    run_agent(session, ACME_OPERATOR, "Which devices are low on disk space?", llm=prov)
    first = be.requests[0]["body"]
    assert first["output_config"]["format"]["type"] == "json_schema" and first["model"] == "claude-opus-5-5"
    # The tenant is never given to (or chosen by) the model.
    assert all("acme-001" not in json.dumps(q["body"]) for q in be.requests)


def test_circuit_breaker_after_auth_error():
    prov, _ = mock_provider({"plan": [("http", 401)]})
    g = GuardedLLM(prov, stages={"plan", "review", "respond"})
    with pytest.raises(LLMCallError):
        g.complete_json(stage="plan", system="s", user="u", schema=PLAN_SCHEMA)
    assert not g.enabled("respond") and g.calls[0]["error_kind"] == "auth"


# ---- agent behaviour with an LLM ------------------------------------------------------------------
def test_llm_interprets_phrasing_rules_cannot(session):
    baseline = run_agent(session, ACME_OPERATOR, "Which of our laptops are basically out of room?")
    assert baseline["status"] == "cannot_answer"  # rule-based classifier: out of scope
    prov, _ = mock_provider({"plan": [plan("fleet_query", [("find_low_storage_devices", {})])]})
    r = run_agent(session, ACME_OPERATOR, "Which of our laptops are basically out of room?", llm=prov)
    assert r["status"] == "answered" and tools(r) == ["find_low_storage_devices"]
    assert r["execution"]["mode"] == "llm" and r["intent_source"] == "llm"


def test_review_follow_up_executes_dependent_step(session):
    def pick(user):
        from app.evaluation.llm_mock import tool_results

        top = tool_results(user)[0]["data"]["devices"][0]["device_id"]
        return review(False, [("get_device_history", {"device_id": top, "metric": "battery"})])

    prov, _ = mock_provider({"plan": [plan("insight_request", [("get_risk_scores", {})])], "review": [pick]})
    r = run_agent(session, ACME_OPERATOR, "Find our riskiest device and show its battery history", llm=prov)
    assert tools(r) == ["get_risk_scores", "get_device_history"]
    assert r["grounding"]["replan_sources"] == ["llm_review"]
    assert any(s["tool"] == "get_device_history" for s in r["plan"])


def test_review_cannot_add_action_tools_or_exceed_budget(session):
    bad = review(False, [("create_upgrade_order", {"device_id": "MT7PJB7N5LRE", "component": "storage", "spec": "x"}),
                         ("get_fleet_overview", {}), ("get_os_distribution", {}), ("analyze_battery_health", {}),
                         ("analyze_memory_usage", {})])
    prov, _ = mock_provider({"plan": [plan("fleet_query", [("find_low_storage_devices", {})])], "review": [bad]})
    r = run_agent(session, ACME_OPERATOR, "Which devices are low on disk space?", llm=prov)
    assert "create_upgrade_order" not in tools(r) and r["proposals"] == []
    assert len(tools(r)) <= 1 + 3  # initial step + MAX_FOLLOWUP_STEPS


def test_llm_action_hints_fill_missing_component(fresh):
    prov, _ = mock_provider({"plan": [plan("action_request", [("find_low_storage_devices", {"include_deteriorating": False})],
                                           action={"action_type": "create_upgrade_order", "component": "storage"})]})
    r = run_agent(fresh, ACME_OPERATOR, "Get more room for the machines that are running out", llm=prov)
    assert r["status"] == "awaiting_approval"
    assert {p["params"]["component"] for p in r["proposals"]} == {"storage"}
    assert all(p["status"] == "pending_approval" for p in r["proposals"])


def test_llm_cannot_downgrade_unsupported_data(session):
    prov, _ = mock_provider({"plan": [plan("fleet_query", [("get_fleet_overview", {})])]})
    r = run_agent(session, ACME_OPERATOR, "What is the average CPU usage per device?", llm=prov)
    assert r["status"] == "cannot_answer" and r["tools_used"] == []


def test_hybrid_mode_composes_deterministically(session):
    prov, be = mock_provider({"plan": [plan("fleet_query", [("find_low_storage_devices", {})])]})
    r = run_agent(session, ACME_OPERATOR, "Which devices are low on disk space?", llm=prov,
                  settings=S(llm_stages="plan"))
    assert r["execution"]["mode"] == "hybrid" and r["grounding"]["method"] == "deterministic"
    assert {q["stage"] for q in be.requests} == {"plan", "review"}


def test_multi_turn_context_deterministic(session):
    hist = [{"role": "user", "content": "Show 8NM23J95R5I6"},
            {"role": "assistant", "content": "...", "devices": ["8NM23J95R5I6"]}]
    r = run_agent(session, ACME_OPERATOR, "What about its battery?", history=hist)
    assert r["context"]["carried_device_ids"] == ["8NM23J95R5I6"]
    assert any(t["args"].get("device_id") == "8NM23J95R5I6" for t in r["tools_used"])


def test_multi_turn_history_reaches_planner(session):
    prov, be = mock_provider({"plan": [plan("device_lookup", [("get_device_details", {"device_id": "MT7PJB7N5LRE"})])]})
    hist = [{"role": "user", "content": "low disk?"},
            {"role": "assistant", "content": "MT7PJB7N5LRE is low.", "devices": ["MT7PJB7N5LRE"]}]
    run_agent(session, ACME_OPERATOR, "Who owns that one?", history=hist, llm=prov)
    assert "devices referenced: MT7PJB7N5LRE" in be.requests[0]["user"]


def test_clarification_returned_without_tools(session):
    prov, _ = mock_provider({"plan": [plan("fleet_query", clarification="Which device do you mean?")]})
    r = run_agent(session, ACME_OPERATOR, "Is it bad?", llm=prov)
    assert r["status"] == "needs_clarification" and r["tools_used"] == []
    assert "Which device do you mean?" in r["answer"]


def test_timeline_exposes_plan_tools_and_llm_calls(session):
    prov, _ = mock_provider({"plan": [plan("fleet_query", [("find_low_storage_devices", {})])]})
    r = run_agent(session, ACME_OPERATOR, "Which devices are low on disk space?", llm=prov)
    from sqlalchemy import select

    from app.models import AgentStep

    steps = list(session.scalars(select(AgentStep).where(AgentStep.run_id == r["run_id"]).order_by(AgentStep.seq)))
    types = [s.step_type for s in steps]
    for t in ("intent", "llm_call", "plan", "tool_selection", "tool_call", "evidence_validation", "response"):
        assert t in types
    call = next(s for s in steps if s.step_type == "tool_call")
    assert call.output["output"]["result_preview"] and call.input["args"] == {}
    llm = next(s for s in steps if s.step_type == "llm_call")
    assert llm.output["model"] == "claude-opus-5-5" and llm.output["token_usage"]["input_tokens"] > 0
