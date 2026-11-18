"""Response composition.

* `deterministic_answer` builds the answer from tool summaries (grounded by construction).
* `verify_grounding` checks an LLM-written answer: every device/employee ID and every number it
  mentions must occur in the tool results, and every cited evidence ID must be validated evidence.
  If any check fails the LLM answer is discarded and the deterministic answer is used instead.
"""

from __future__ import annotations

import json
import re

from app.agents.planning import DEVICE_ID_RE, EMPLOYEE_ID_RE

TOOL_TITLES = {
    "get_fleet_overview": "Fleet overview",
    "get_device_details": "Device details",
    "find_low_storage_devices": "Storage",
    "analyze_memory_usage": "Memory",
    "analyze_battery_health": "Battery health",
    "get_compliance_status": "Compliance status",
    "analyze_compliance_trends": "Compliance trends",
    "get_os_distribution": "Operating systems",
    "get_installed_software": "Installed software",
    "generate_fleet_insights": "Fleet insights",
    "get_risk_scores": "Risk scores",
    "get_device_history": "Device history",
    "get_employee_devices": "Employee devices",
}
POLICY_TOOLS = {"find_low_storage_devices", "analyze_memory_usage", "analyze_battery_health", "get_os_distribution",
                "generate_fleet_insights", "get_risk_scores"}

HELP_TEXT = (
    "I can answer questions about your company's devices using their telemetry, for example:\n"
    "- *Which devices are low on disk space?*\n- *Which devices have consistently high RAM usage?*\n"
    "- *Show me devices failing high-severity compliance checks.*\n- *How many devices run an OS older than macOS 15?*\n"
    "- *What are the biggest fleet health risks this month?*\n- *Show the history of device 8NM23J95R5I6.*\n\n"
    "I can also **propose** actions — upgrade orders, remediation tickets, replacement flags and employee "
    "notifications. Proposals are never executed until an administrator approves them in the Approval Center."
)
SUPPORTED_DATA = ("OS versions, RAM usage, disk space, battery condition/cycles/capacity, installed software, "
                  "network interfaces and compliance checks over ~30 days of daily snapshots")


def _bullets_for(tr: dict) -> list[str]:
    d = tr.get("data", {})
    if tr["tool"] == "generate_fleet_insights":
        lines = [f"- **[{i['severity']}] {i['title']}** — {i['finding']} _Next step: {i['recommended_next_step']}_"
                 for i in d.get("insights", [])[:10]]
        top = d.get("top_risk_devices", [])
        if top:
            lines.append("- **Highest-risk devices:** " + ", ".join(
                f"{r['device_id']} ({r['overall_score']}/100, {r['classification']})" for r in top))
        return lines
    if tr["tool"] == "get_risk_scores":
        return [f"- {r['device_id']} — {r['overall_score']}/100 **{r['classification']}**; "
                + "; ".join(r["contributing_factors"][:3]) for r in d.get("devices", [])[:15]]
    return []


def deterministic_answer(state: dict) -> str:
    intent = state.get("intent")
    guard = state.get("guard") or {}
    if guard.get("blocked"):
        return f"**Request not processed.** {guard.get('reason')}"
    if state.get("clarification") and not state.get("tool_results"):
        return ("**Clarification needed:** " + _safe_clarification(state["clarification"])
                + "\n\nNothing was queried or proposed yet.")
    if intent == "help":
        return HELP_TEXT
    if intent == "out_of_scope":
        return ("I can only help with your company's device fleet — its telemetry covers " + SUPPORTED_DATA
                + ". Try asking about device health, compliance or risks.")
    if intent == "unsupported_data":
        missing = ", ".join((state.get("entities") or {}).get("unsupported") or ["that information"])
        return (f"**Not available in telemetry:** the dataset does not contain {missing}, so I can't answer that "
                f"without guessing. Available data: {SUPPORTED_DATA}.")

    parts: list[str] = []
    results = state.get("tool_results") or []
    ok = [r for r in results if r["ok"] and r.get("kind") != "action"]
    failed = [r for r in results if not r["ok"] and r.get("kind") != "action"]
    for r in ok:
        parts.append(f"**{TOOL_TITLES.get(r['tool'], r['tool'])}** — {r['summary']}")
        parts.extend(_bullets_for(r))
    for r in failed:
        if r.get("error_code") == "not_found":
            parts.append(f"**Not found:** {r['error']}.")
        else:
            parts.append(f"**{TOOL_TITLES.get(r['tool'], r['tool'])} unavailable** ({r.get('error_code')}): {r['error']}")

    proposals = state.get("proposals") or []
    refused = state.get("refused_actions") or []
    if proposals:
        parts.append("**Proposed actions (awaiting human approval — nothing has been executed):**")
        for p in proposals:
            target = p["params"].get("device_id") or p["params"].get("employee_id")
            reused = "" if p.get("created") else " (an identical open proposal already existed)"
            parts.append(f"- `{p['action_type']}` for {target} — proposal `{p['proposal_id'][:8]}`{reused}. "
                         f"Evidence: {p['justification']}")
    if refused:
        parts.append("**Not proposed:**")
        for r in refused:
            target = r["params"].get("device_id") or r["params"].get("employee_id") or "request"
            parts.append(f"- `{r['action_type']}` for {target}: {r['reason']}")
    if intent == "action_request" and not proposals and not refused:
        parts.append("No action was proposed: the telemetry did not identify any target that meets the evidence "
                     "requirements for this action.")

    validation = state.get("validation") or {}
    if validation.get("status") == "insufficient":
        parts.append(f"**Insufficient evidence:** {validation.get('reason')}")
    if any(r["tool"] in POLICY_TOOLS for r in ok):
        parts.append("_Thresholds (e.g. low storage < 10% free, RAM pressure > 80% used, persistent = ≥ 50% of "
                     "snapshots, minimum OS versions) are configurable policy settings, not dataset facts._")
    return "\n\n".join(parts) if parts else "I could not find telemetry that answers this question."


def _safe_clarification(text: str) -> str:
    """The clarification question comes from the LLM: keep it short, plain text, and free of device/employee
    IDs or numbers (it must not assert facts that no tool produced)."""
    text = re.sub(r"[<>`*_#\[\]]", "", text).strip()[:300]
    if DEVICE_ID_RE.search(text) or EMPLOYEE_ID_RE.search(text) or NUM_RE.search(text):
        return "Could you clarify which devices, metric or time period you mean?"
    return text or "Could you clarify which devices, metric or time period you mean?"


# --------------------------------------------------------------------------------------------
NUM_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
LIST_MARKER_RE = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)


def _norm_num(s: str) -> str:
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f == int(f) else str(round(f, 2))


def verify_grounding(answer: str, cited: list[str], tool_payload: str, evidence_ids: set[str]) -> list[str]:
    issues: list[str] = []
    allowed_devices = set(DEVICE_ID_RE.findall(tool_payload))
    allowed_emps = {e.lower() for e in EMPLOYEE_ID_RE.findall(tool_payload)}
    for d in set(DEVICE_ID_RE.findall(answer)):
        if d not in allowed_devices:
            issues.append(f"device id {d} not present in tool results")
    for emp in {e.lower() for e in EMPLOYEE_ID_RE.findall(answer)}:
        if emp not in allowed_emps:
            issues.append(f"employee id {emp} not present in tool results")
    source_nums = {_norm_num(n) for n in NUM_RE.findall(tool_payload)}
    body = LIST_MARKER_RE.sub("", EMPLOYEE_ID_RE.sub("", answer))
    body = DEVICE_ID_RE.sub("", body)
    for n in set(NUM_RE.findall(body)):
        if _norm_num(n) not in source_nums:
            issues.append(f"number {n} not present in tool results")
    for c in cited:
        if c not in evidence_ids:
            issues.append(f"cited evidence {c} does not exist")
    return issues


def compact_results(results: list[dict]) -> list[dict]:
    return [{"tool": r["tool"], "ok": r["ok"], "summary": r.get("summary"), "error": r.get("error"),
             "data": r.get("data")} for r in results]


def tool_payload_text(results: list[dict], max_chars: int = 24000) -> str:
    return json.dumps(compact_results(results), default=str)[:max_chars]
