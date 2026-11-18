"""Evidence-sufficiency rules for each action type.

The *server* gathers the supporting evidence for a proposed action from telemetry; it does not
trust evidence or justification supplied by the LLM. If the rule for an action is not satisfied,
the proposal is refused with `InsufficientEvidenceError`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics.fleet import FleetAnalysis
from app.analytics.risk import score_device
from app.core.errors import InsufficientEvidenceError, ValidationFailedError
from app.schemas.evidence import Evidence


@dataclass
class Support:
    evidence: list[Evidence]
    justification: str


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise InsufficientEvidenceError(msg)


def support_upgrade(fa: FleetAnalysis, device_id: str, component: str) -> Support:
    if component == "storage":
        f = fa.storage.get(device_id)
        _need(f is not None, "no storage telemetry for this device")
        st = fa.cfg.storage
        low = f.current["available_pct"] < st.low_pct
        det = (f.trend or {}).get("direction") == "deteriorating" and f.current["available_pct"] < st.warn_pct
        _need(low or det, f"storage is {f.current['available_pct']}% free, above the {st.low_pct}% policy threshold "
                          "and not deteriorating — an upgrade is not justified by telemetry")
        return Support(f.evidence, f"Disk {f.current['available_pct']}% free in latest snapshot "
                                   f"({f.pattern.replace('_', ' ')}; {f.trend['breach_count']}/{f.trend['n']} snapshots below {st.low_pct}%).")
    if component == "memory":
        f = fa.memory.get(device_id)
        _need(f is not None, "no memory telemetry for this device")
        p = (f.trend or {}).get("persistence")
        _need(p == "persistent", f"RAM pressure is '{p}', not persistent — a one-off or occasional spike does not "
                                 "justify a hardware upgrade")
        return Support(f.evidence, f"RAM above {fa.cfg.memory.high_pct}% in {f.trend['breach_count']}/{f.trend['n']} "
                                   f"snapshots (mean {f.trend['mean_value']}%).")
    if component == "battery":
        f = fa.battery.get(device_id)
        _need(f is not None and f.pattern != "no_battery_reported", "device reports no battery telemetry")
        c = f.current
        decline_ok = c.get("capacity_decline_pct") is not None and c["capacity_decline_pct"] >= fa.cfg.battery.capacity_decline_pct
        _need(c.get("service_recommended") or decline_ok or c.get("high_cycle_count"),
              f"battery condition '{c.get('condition')}' with {c.get('cycle_count')} cycles does not meet replacement criteria")
        return Support(f.evidence, f"Battery '{c['condition']}', {c['cycle_count']} cycles, capacity change "
                                   f"-{c.get('capacity_decline_pct')}% over the window.")
    raise InsufficientEvidenceError(f"no telemetry rule supports a '{component}' upgrade")


def support_remediation(fa: FleetAnalysis, device_id: str, check_id: str) -> Support:
    f = fa.compliance.get(device_id)
    _need(f is not None, "no compliance telemetry for this device")
    check = next((c for c in f.current["checks"] if c["check_id"] == check_id), None)
    if check is None:
        known = ", ".join(c["check_id"] for c in f.current["checks"])
        raise ValidationFailedError(f"unknown check_id '{check_id}' for this device (known: {known})")
    _need(check["latest_status"] == "fail", f"'{check_id}' is passing in the latest snapshot — nothing to remediate")
    evid = [e for e in f.evidence if e.metric.endswith(check_id)]
    return Support(evid, f"'{check_id}' ({check['severity']}) failing in latest snapshot; pattern "
                         f"{check['pattern']} ({check['fail_count']}/{check['snapshots']} snapshots failed).")


def support_replacement(fa: FleetAnalysis, device_id: str) -> Support:
    risk = score_device(fa, device_id)
    _need(risk.classification in {"critical", "at_risk"},
          f"device risk is {risk.overall_score}/100 ({risk.classification}); replacement requires at_risk or critical")
    return Support(risk.evidence, f"Risk {risk.overall_score}/100 ({risk.classification}): "
                                  + "; ".join(risk.contributing_factors[:4]))


def support_notification(fa: FleetAnalysis, employee_id: str) -> Support:
    devices = [d for d, m in fa.device_meta.items() if m["employee_id"] == employee_id]
    _need(bool(devices), "employee has no devices in telemetry")
    risks = [score_device(fa, d) for d in devices]
    flagged = [r for r in risks if r.classification != "healthy"]
    sw = [fa.software[d] for d in devices if d in fa.software]
    _need(bool(flagged) or bool(sw), "none of this employee's devices has a telemetry finding that warrants a notification")
    evid = [e for r in flagged for e in r.evidence] + [e for f in sw for e in f.evidence]
    return Support(evid, "Devices with findings: " + ", ".join(
        f"{r.device_id} ({r.classification}, top issue {r.top_category})" for r in flagged)
        + ("; watch-listed software present" if sw else ""))
