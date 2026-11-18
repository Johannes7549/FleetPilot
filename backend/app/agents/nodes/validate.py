"""Evidence validation node.

Re-checks every evidence reference produced by tools against the database *for this tenant*:
company matches the session, referenced snapshots exist in the tenant, referenced devices exist in
the tenant. Invalid evidence is dropped and reported. Then decides whether the evidence is
sufficient to answer, whether a re-plan is worthwhile, or whether to stop with a clarification.
"""

from __future__ import annotations

import json
import time

from app.agents.composer import compact_results
from app.agents.llm.providers import LLMCallError
from app.agents.nodes.understand import STEP_SCHEMA, _catalog, _history_text, validate_llm_steps
from app.agents.prompts import load
from app.agents.state import AgentState, deps_of
from app.agents.timeline import event
from app.guardrails.injection import sanitize_payload, wrap_untrusted
from app.schemas.evidence import ev

REPLANNABLE = {"invalid_arguments", "unknown_tool", "forbidden_argument"}
REVIEW_INTENTS = ("fleet_query", "insight_request", "device_lookup")
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"complete": {"type": "boolean"}, "missing": {"type": "string"},
                   "follow_up_steps": {"type": "array", "items": STEP_SCHEMA}},
    "required": ["complete", "missing", "follow_up_steps"],
    "additionalProperties": False,
}
MAX_FOLLOWUP_STEPS = 3


def _sig(tool: str, args: dict) -> tuple[str, str]:
    return tool, json.dumps(args or {}, sort_keys=True)


def _llm_review(state: AgentState, deps, ok_results: list[dict], t0: float) -> tuple[list[dict], list[dict], list[dict]]:
    """LLM inspects (sanitised) tool results and may request bounded follow-up READ steps - e.g. look up the
    device a first tool identified. Returns (follow_up_steps, events, usage)."""
    executed = {_sig(r["tool"], r["args"]) for r in ok_results}
    budget = min(MAX_FOLLOWUP_STEPS, deps.settings.agent_max_tool_calls - len(executed))
    if budget <= 0:
        return [], [], []
    flags: list[str] = []
    safe = json.dumps(sanitize_payload(json.loads(json.dumps(compact_results(ok_results), default=str)), flags))[:12000]
    user = (f"Recent conversation (context only):\n{_history_text(state.get('history') or []) or '(none)'}\n\n"
            f"Tools already executed: {json.dumps([{'tool': t, 'args': json.loads(a)} for t, a in sorted(executed)])}\n"
            f"Follow-up budget: at most {budget} read step(s).\n\n"
            + wrap_untrusted("request", state["question"]) + "\n\n" + wrap_untrusted("tool_results", safe))
    try:
        res = deps.llm.complete_json(stage="review", system=load("reviewer").replace("{catalog}", _catalog()),
                                     user=user, schema=REVIEW_SCHEMA, max_tokens=1200)
    except LLMCallError as exc:
        return [], [event("llm_call", "LLM result review", status="error", started=t0,
                          input={"stage": "review"}, output={"error_kind": exc.kind, "error": exc.message,
                                                             "fallback": "accept current results"})], []
    steps, issues = validate_llm_steps(res.data.get("follow_up_steps") or [], limit=budget)
    steps = [s for s in steps if _sig(s["tool"], s["args"]) not in executed]
    if res.data.get("complete"):
        steps = []
    evs = [event("llm_call", "LLM result review", started=t0, token_usage=res.usage, model=res.model,
                 input={"stage": "review", "tools_reviewed": [r["tool"] for r in ok_results]},
                 output={"complete": res.data.get("complete"), "missing": str(res.data.get("missing", ""))[:300],
                         "follow_up_steps": steps, "validation_issues": issues, "injection_flags": flags[:10]})]
    return steps, evs, [{"stage": "review", **res.usage, "model": res.model}]


def validate_evidence(state: AgentState, config) -> dict:
    t0 = time.perf_counter()
    deps = deps_of(config)
    repo = deps.tool_ctx.repo
    cid = deps.tenant.company_id
    results = [r for r in state.get("tool_results") or [] if r.get("kind") == "read"]
    ok = [r for r in results if r["ok"]]
    # A failure is superseded when a later call of the same tool succeeded (after a re-plan repaired it).
    failed = [r for i, r in enumerate(results) if not r["ok"]
              and not any(x["ok"] and x["tool"] == r["tool"] for x in results[i + 1:])]

    evidence: dict[str, dict] = {}
    issues: list[str] = []
    for r in ok:
        items = r.get("evidence") or []
        if not items:
            # An empty result is still a grounded answer ("no records match"); record the query itself.
            items = [ev(company_id=cid, metric=f"{r['tool']}:matching_records", observed_value=0,
                        source=r["tool"], temporal_scope="latest", note=f"args={r['args']}").model_dump(mode="json")]
            r["evidence"] = items
        for e in items:
            evidence.setdefault(e["evidence_id"], e)

    foreign = [e for e in evidence.values() if e["company_id"] != cid]
    snap_ids = {e["snapshot_id"] for e in evidence.values() if e.get("snapshot_id")}
    existing = repo.snapshot_ids_exist(snap_ids)
    missing_snaps = snap_ids - existing
    dev_ids = {e["device_id"] for e in evidence.values() if e.get("device_id")}
    tenant_devices = {d.id for d in repo.devices()}
    missing_devs = dev_ids - tenant_devices
    if foreign:
        issues.append(f"{len(foreign)} evidence item(s) from another tenant removed")
    if missing_snaps:
        issues.append(f"{len(missing_snaps)} evidence item(s) reference unknown snapshots — removed")
    if missing_devs:
        issues.append(f"{len(missing_devs)} evidence item(s) reference unknown devices — removed")
    valid = [e for e in evidence.values() if e["company_id"] == cid
             and (not e.get("snapshot_id") or e["snapshot_id"] in existing)
             and (not e.get("device_id") or e["device_id"] in tenant_devices)]

    replannable = [r for r in failed if r.get("error_code") in REPLANNABLE]
    if (state.get("intent") in ("help", "out_of_scope", "unsupported_data") or (state.get("guard") or {}).get("blocked")
            or (state.get("clarification") and not results)):
        status, reason = "not_applicable", None
    elif ok and valid:
        status, reason = ("sufficient" if not failed else "partial"), None
    elif replannable and state.get("replans", 0) < deps.settings.agent_max_replans:
        status, reason = "replan", "; ".join(f"{r['tool']}: {r['error']}" for r in replannable)
    elif failed and all(r.get("error_code") == "not_found" for r in failed):
        status, reason = "not_found", "; ".join(r["error"] for r in failed)
    else:
        status = "insufficient"
        reason = ("; ".join(f"{r['tool']}: {r['error']}" for r in failed) if failed
                  else "no tool returned usable evidence for this request")

    validation = {"status": status, "reason": reason, "issues": issues, "evidence_count": len(valid),
                  "tools_ok": len(ok), "tools_failed": len(failed),
                  "checks": {"tenant_match": not foreign, "snapshots_exist": not missing_snaps,
                             "devices_in_tenant": not missing_devs}}
    timeline = [event("evidence_validation", "Evidence validated",
                      status="ok" if status in ("sufficient", "not_applicable") else "warning",
                      output=dict(validation), evidence_ids=[e["evidence_id"] for e in valid][:200], started=t0)]
    out: dict = {"validation": validation, "evidence": valid, "timeline": timeline}

    # Inspect results with the LLM (bounded): it may ask for follow-up read steps that depend on what the
    # first tools returned. Evidence validation above runs again after the follow-ups execute.
    if (status in ("sufficient", "partial") and deps.llm is not None and deps.llm.enabled("review")
            and state.get("intent") in REVIEW_INTENTS and state.get("replans", 0) < deps.settings.agent_max_replans):
        steps, evs, usage = _llm_review(state, deps, ok, time.perf_counter())
        timeline += evs
        out["token_usage"] = usage
        if steps:
            validation.update(status="replan", reason="LLM review requested follow-up steps: "
                              + ", ".join(s["tool"] for s in steps))
            out["followup_steps"] = steps
            status = "replan"
    if status == "replan":
        out["replans"] = state.get("replans", 0) + 1
    return out
