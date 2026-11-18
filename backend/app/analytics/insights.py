"""Fleet insight generation. Every insight carries: finding, evidence, device ids, timestamps,
explanation, severity, pattern type and recommended next step — all computed deterministically."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.analytics.fleet import FleetAnalysis
from app.schemas.evidence import Evidence

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class Insight(BaseModel):
    insight_id: str
    category: str
    title: str
    finding: str
    severity: str
    pattern_type: str  # persistent | deteriorating | confirmed_failure | drift | one_time | policy | data_quality | none
    explanation: str
    device_ids: list[str]
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    recommended_next_step: str
    recommended_actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


def _iid(*parts: object) -> str:
    return "ins_" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _times(evs: list[Evidence]) -> tuple[datetime | None, datetime | None]:
    ts = [e.collected_at for e in evs if e.collected_at]
    return (min(ts), max(ts)) if ts else (None, None)


def generate_insights(fa: FleetAnalysis) -> list[Insight]:
    cfg, cid = fa.cfg, fa.cid
    out: list[Insight] = []

    def add(category, title, finding, severity, pattern, explanation, devices, evidence, next_step, actions=()):
        first, last = _times(evidence)
        out.append(Insight(insight_id=_iid(cid, category, title, ",".join(sorted(devices))), category=category,
                           title=title, finding=finding, severity=severity, pattern_type=pattern,
                           explanation=explanation, device_ids=sorted(devices), first_observed_at=first,
                           last_observed_at=last, recommended_next_step=next_step,
                           recommended_actions=list(actions), evidence=evidence))

    # ---- storage --------------------------------------------------------------------------
    st = cfg.storage
    det = [f for f in fa.storage.values() if f.trend and f.trend["direction"] == "deteriorating" and f.current["available_pct"] < st.warn_pct]
    low_persistent = [f for f in fa.storage.values() if f.trend and f.trend["persistence"] == "persistent"]
    critical = [f for f in fa.storage.values() if f.current["below_critical_threshold"]]
    if critical:
        add("storage", "Critically low storage",
            f"{len(critical)} device(s) have under {st.critical_pct}% disk free in their latest snapshot: "
            + ", ".join(f"{f.device_id} ({f.current['available_pct']}%)" for f in critical),
            "critical", "confirmed_failure",
            f"Latest snapshot per device compared with the critical threshold ({st.critical_pct}% available, policy).",
            [f.device_id for f in critical], [e for f in critical for e in f.evidence],
            "Free space or expand storage immediately; contact the device owners.",
            [{"tool": "create_upgrade_order", "args": {"device_id": f.device_id, "component": "storage",
              "spec": "Increase storage capacity"}} for f in critical])
    if det:
        add("storage", "Storage steadily running out",
            "Free disk space is declining steadily on " + ", ".join(
                f"{f.device_id} ({f.trend['first_value']}% -> {f.trend['latest_value']}%, {f.trend['slope_per_day']} pp/day)" for f in det),
            "high", "deteriorating",
            f"Least-squares slope of available % across all snapshots is at or below "
            f"{st.deteriorating_slope_pp_per_day} pp/day. This is a trend, not a one-off reading.",
            [f.device_id for f in det], [e for f in det for e in f.evidence],
            "Investigate what is consuming disk (logs, caches, sync folders) before the device fills up.")
    if low_persistent:
        add("storage", "Devices consistently low on storage",
            "Below the low-storage threshold in most snapshots: " + ", ".join(
                f"{f.device_id} ({f.trend['breach_count']}/{f.trend['n']})" for f in low_persistent),
            "high", "persistent",
            f"'Persistent' = below {st.low_pct}% available in >= {int(cfg.trends.persistent_ratio * 100)}% of snapshots.",
            [f.device_id for f in low_persistent], [e for f in low_persistent for e in f.evidence],
            "Schedule storage upgrades or replacement for these devices.")

    # ---- memory ---------------------------------------------------------------------------
    mt = cfg.memory
    pers = [f for f in fa.memory.values() if f.trend and f.trend["persistence"] == "persistent"]
    spiky = [f for f in fa.memory.values() if f.trend and f.trend["persistence"] in {"one_time", "intermittent"}]
    if pers:
        add("memory", "Devices consistently constrained by RAM",
            "RAM usage above the threshold in most snapshots: " + ", ".join(
                f"{f.device_id} ({f.trend['breach_count']}/{f.trend['n']}, mean {f.trend['mean_value']}%)" for f in pers),
            "high", "persistent",
            f"Used memory > {mt.high_pct}% in >= {int(cfg.trends.persistent_ratio * 100)}% of snapshots. "
            "A single high reading would be classified as a one-time spike instead.",
            [f.device_id for f in pers], [e for f in pers for e in f.evidence],
            "Upgrade RAM where the hardware allows it, otherwise plan replacement.",
            [{"tool": "create_upgrade_order", "args": {"device_id": f.device_id, "component": "memory",
              "spec": "Upgrade RAM"}} for f in pers])
    if spiky:
        add("memory", "Temporary RAM spikes (not persistent)",
            "Occasional high RAM usage only: " + ", ".join(
                f"{f.device_id} ({f.trend['breach_count']}/{f.trend['n']})" for f in spiky),
            "low", "one_time",
            "These devices exceeded the threshold in too few snapshots to be a persistent problem.",
            [f.device_id for f in spiky], [e for f in spiky for e in f.evidence], "Monitor; no action required yet.")

    # ---- battery --------------------------------------------------------------------------
    svc = [f for f in fa.battery.values() if f.current.get("service_recommended")]
    if svc:
        def when(f):
            t = [x for x in f.current["condition_transitions"] if x["to"] in cfg.battery.service_conditions]
            return f" since {t[0]['collected_at']:%Y-%m-%d}" if t else ""
        add("battery", "Batteries approaching end-of-life",
            "Battery condition is 'Service Recommended' on " + ", ".join(
                f"{f.device_id} ({f.current['cycle_count']} cycles{when(f)}, capacity -{f.current['capacity_decline_pct']}%)" for f in svc),
            "high", "deteriorating",
            "Condition reported by the device OS changed to a service state during the window; full-charge capacity "
            "fell at the same time. Note: design capacity is not reported, so absolute battery health % is unavailable.",
            [f.device_id for f in svc], [e for f in svc for e in f.evidence],
            "Order battery replacements and notify the device owners.",
            [{"tool": "create_upgrade_order", "args": {"device_id": f.device_id, "component": "battery",
              "spec": "Replacement battery"}} for f in svc])
    gaps = [f for f in fa.battery.values() if f.current.get("snapshots_missing_battery")]
    if gaps:
        add("data_quality", "Intermittent battery telemetry gaps",
            "Some snapshots of these laptops lack a battery block: " + ", ".join(
                f"{f.device_id} ({f.current['snapshots_missing_battery']} snapshots"
                f"{', including latest' if f.current['latest_snapshot_missing_battery'] else ''})" for f in gaps),
            "info", "data_quality",
            "Missing telemetry is reported as unknown, never as a battery failure.",
            [f.device_id for f in gaps], [e for f in gaps for e in f.evidence[:1]],
            "Check the telemetry agent on these devices if gaps persist.")

    # ---- compliance -----------------------------------------------------------------------
    regressed: dict[str, list] = {}
    persistent_fail: dict[str, list] = {}
    high_fail = []
    for f in fa.compliance.values():
        for c in f.current["checks"]:
            if c["pattern"] == "regressed":
                regressed.setdefault(c["check_id"], []).append((f, c))
            if c["pattern"] == "persistent_failure":
                persistent_fail.setdefault(c["check_id"], []).append((f, c))
            if c["latest_status"] == "fail" and c["severity"] == "high":
                high_fail.append((f, c))
    for check, items in regressed.items():
        evs = [e for f, c in items for e in f.evidence if e.metric in (f"compliance:{check}", f"compliance_change:{check}")]
        add("compliance", f"Compliance drift: {check} regressed",
            f"{len(items)} device(s) passed '{check}' earlier in the window and fail it now: " + ", ".join(
                f"{f.device_id} (since {c['transitions'][-1]['collected_at']:%Y-%m-%d})" for f, c in items),
            "medium" if items[0][1]["severity"] != "high" else "critical", "drift",
            "Detected as a pass->fail transition that has not recovered by the latest snapshot.",
            [f.device_id for f, _ in items], evs,
            f"Open remediation tickets for '{check}' and check for a policy/profile change around the regression dates.",
            [{"tool": "open_remediation_ticket", "args": {"device_id": f.device_id, "check_id": check,
              "note": f"{check} regressed to fail"}} for f, _ in items])
    for check, items in persistent_fail.items():
        evs = [e for f, c in items for e in f.evidence if e.metric == f"compliance:{check}"]
        add("compliance", f"Persistent compliance failure: {check}",
            f"{len(items)} device(s) failed '{check}' in every snapshot of the window.",
            "medium" if items[0][1]["severity"] != "high" else "critical", "persistent",
            "Failure is present in all snapshots — a confirmed, long-standing failure rather than drift.",
            [f.device_id for f, _ in items], evs, f"Plan remediation for '{check}' across these devices.")
    if not high_fail and fa.compliance:
        add("compliance", "No high-severity compliance failures",
            "No device fails a high-severity compliance check in its latest snapshot.", "info", "none",
            "All latest high-severity results are 'pass'.", [], [], "No action needed.")

    # ---- OS -------------------------------------------------------------------------------
    old = [f for f in fa.os.values() if f.current["below_policy_minimum"]]
    if old:
        add("os", "Operating systems below policy minimum",
            f"{len(old)} device(s) run an OS major version below policy: " + ", ".join(
                f"{f.device_id} ({f.current['os']} {f.current['version']})" for f in old),
            "medium", "policy",
            "Policy minimums: " + ", ".join(f"{k} {v}" for k, v in cfg.os.min_major_versions.items())
            + " (configurable). No OS upgrades were observed during the window.",
            [f.device_id for f in old], [e for f in old for e in f.evidence], "Schedule OS upgrades.")

    # ---- software -------------------------------------------------------------------------
    sw = list(fa.software.values())
    if sw:
        add("software", "Watch-listed software appeared",
            "; ".join(f"{f.device_id}: " + ", ".join(f"{h['name']} (first seen {h['first_seen_at']:%Y-%m-%d})"
                                                      for h in f.current["watchlisted_software"]) for f in sw),
            "medium", "drift",
            "Packages on the configurable software watch-list (policy) were first observed mid-window.",
            [f.device_id for f in sw], [e for f in sw for e in f.evidence],
            "Confirm whether the software is approved; notify the employee if not.",
            [{"tool": "notify_employee", "args": {"employee_id": f.employee_id,
              "message": "Please review watch-listed software installed on your device."}} for f in sw if f.employee_id])

    return sorted(out, key=lambda i: (SEVERITY_ORDER[i.severity], i.category, i.title))
