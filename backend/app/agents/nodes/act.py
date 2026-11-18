"""Action proposal node.

Derives candidate actions from the user's explicit request plus the evidence gathered by read tools,
then calls the action tools. Action tools only *propose* (status `pending_approval`); the action
service re-validates tenant ownership, parameters and evidence sufficiency on its own, so nothing
decided here can bypass those checks. A per-request cap bounds blast radius.
"""

from __future__ import annotations

import time

from app.agents.state import AgentState, deps_of
from app.agents.timeline import event
from app.tools import registry

MAX_PROPOSALS_PER_REQUEST = 10
DEFAULT_SPEC = {"memory": "RAM upgrade (e.g. 32 GB)", "storage": "Storage upgrade (e.g. 1 TB SSD)",
                "battery": "Replacement battery"}


def _candidates(state: AgentState) -> list[tuple[str, dict]]:
    e = state["entities"]
    action = e.get("action_type")
    comp = e.get("component")
    results = {r["tool"]: r for r in state.get("tool_results") or [] if r["ok"]}
    cands: list[tuple[str, dict]] = []

    device_ids: list[str] = list(e.get("device_ids") or [])
    if not device_ids and not e.get("employee_ids"):
        if "find_low_storage_devices" in results:
            device_ids = [d["device_id"] for d in results["find_low_storage_devices"]["data"]["low_storage_devices"]]
        elif "analyze_memory_usage" in results:
            device_ids = [d["device_id"] for d in results["analyze_memory_usage"]["data"]["devices"]
                          if d["trend"]["persistence"] == "persistent"]
        elif "analyze_battery_health" in results:
            device_ids = [d["device_id"] for d in results["analyze_battery_health"]["data"]["devices"]
                          if d["current"].get("service_recommended")]
        elif "get_compliance_status" in results:
            seen = []
            for r in results["get_compliance_status"]["data"]["results"]:
                if r["device_id"] not in seen:
                    seen.append(r["device_id"])
            device_ids = seen
        elif "get_risk_scores" in results:
            device_ids = [d["device_id"] for d in results["get_risk_scores"]["data"]["devices"]]

    if action == "notify_employee":
        emps = list(e.get("employee_ids") or [])
        if not emps:
            owners = {}
            for tr in results.values():
                for key in ("low_storage_devices", "devices", "results"):
                    for d in tr["data"].get(key, []) if isinstance(tr["data"].get(key), list) else []:
                        if d.get("device_id") in device_ids and d.get("employee_id"):
                            owners[d["employee_id"]] = None
            if "get_device_details" in results:
                for tr in state["tool_results"]:
                    if tr["tool"] == "get_device_details" and tr["ok"]:
                        owners[tr["data"]["device"]["employee_id"]] = None
            emps = list(owners)
        topic = comp or "device health"
        for emp in emps:
            cands.append(("notify_employee", {"employee_id": emp, "message":
                          f"IT has identified a {topic} issue on a device assigned to you. Please contact IT support "
                          "to schedule remediation."}))
        return cands

    for d in device_ids:
        if action == "open_remediation_ticket":
            checks = [e["check_id"]] if e.get("check_id") else _failing_checks(state, d)
            for c in checks or [e.get("check_id") or "os_up_to_date"]:
                cands.append(("open_remediation_ticket", {"device_id": d, "check_id": c,
                              "note": f"Remediate failing '{c}' compliance check (raised via FleetPilot)."}))
        elif action == "flag_device_for_replacement":
            cands.append(("flag_device_for_replacement", {"device_id": d,
                          "reason": "Flagged via FleetPilot based on device risk assessment."}))
        elif action == "create_upgrade_order":
            component = comp or _recommended_component(state, d)
            # Without a component we propose nothing (the respond node reports why).
            cands.append(("create_upgrade_order", {"device_id": d, "component": component,
                          "spec": DEFAULT_SPEC.get(component or "", "unspecified")}))
    return cands


def _failing_checks(state: AgentState, device_id: str) -> list[str]:
    for tr in state.get("tool_results") or []:
        if tr["ok"] and tr["tool"] == "get_device_details" and tr["data"]["device"]["device_id"] == device_id:
            snap = tr["data"].get("latest_snapshot") or {}
            return [c["check_id"] for c in snap.get("compliance", []) if c["status"] == "fail"]
        if tr["ok"] and tr["tool"] == "get_compliance_status":
            found = [r["check_id"] for r in tr["data"]["results"] if r["device_id"] == device_id]
            if found:
                return found
    return []


def _recommended_component(state: AgentState, device_id: str) -> str | None:
    for tr in state.get("tool_results") or []:
        if tr["ok"] and tr["tool"] == "get_device_details" and tr["data"]["device"]["device_id"] == device_id:
            rec = tr["data"]["risk"].get("recommended_action") or {}
            if rec.get("tool") == "create_upgrade_order":
                return rec["args"]["component"]
    return None


def propose_actions(state: AgentState, config) -> dict:
    t0 = time.perf_counter()
    deps = deps_of(config)
    cands = _candidates(state)
    proposals, refused, results, events = [], [], [], []
    if not deps.tenant.can("actions:propose"):
        refused = [{"action_type": a, "params": p, "reason": f"your role '{deps.tenant.role}' cannot propose actions"}
                   for a, p in cands] or [{"action_type": state["entities"].get("action_type") or "action",
                                            "params": {}, "reason": f"your role '{deps.tenant.role}' cannot propose actions"}]
        cands = []
    if len(cands) > MAX_PROPOSALS_PER_REQUEST:
        refused.append({"action_type": cands[0][0], "params": {},
                        "reason": f"{len(cands)} targets exceed the per-request cap of {MAX_PROPOSALS_PER_REQUEST}; "
                                  "narrow the request"})
        cands = []
    for action_type, params in cands:
        res = registry.execute(action_type, params, deps.tool_ctx)
        out = res.model_dump(mode="json")
        out["kind"] = "action"
        results.append(out)
        if res.ok:
            proposals.append(res.data)
        else:
            refused.append({"action_type": action_type, "params": params, "reason": res.error,
                            "code": res.error_code})
        events.append(event("action_proposal", action_type, status="ok" if res.ok else "refused",
                            input=params, output={"summary": res.summary, "error": res.error,
                                                  "code": res.error_code},
                            evidence_ids=[e.evidence_id for e in res.evidence], duration_ms=res.duration_ms))
    if proposals:
        events.append(event("approval_requested", "Human approval requested",
                            output={"proposal_ids": [p["proposal_id"] for p in proposals],
                                    "note": "Actions execute only after an authorised admin approves them."},
                            started=t0))
    if not cands and not refused:
        events.append(event("action_proposal", "No eligible targets", status="warning",
                            output={"reason": "no target met the evidence requirements"}, started=t0))
    return {"proposals": proposals, "refused_actions": refused, "tool_results": results, "timeline": events}
