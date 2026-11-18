"""Deterministic device risk scoring. Methodology: docs/risk-scoring.md.

Each category yields a 0–100 score from explicit rules. The overall score is
    overall = 0.5 * max(category scores) + 0.5 * weighted_mean(category scores)
so a single critical problem cannot be diluted by four healthy categories, while multiple
moderate problems still accumulate. Bands: critical >= 70, at_risk >= 45, watch >= 20.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.analytics.fleet import FleetAnalysis
from app.schemas.evidence import Evidence

CATEGORIES = ("storage", "memory", "battery", "compliance", "os")


class CategoryScore(BaseModel):
    score: int
    factors: list[str] = Field(default_factory=list)
    issue_type: str = "none"  # none | one_time | intermittent | persistent | deteriorating | confirmed_failure | policy


class DeviceRisk(BaseModel):
    device_id: str
    employee_id: str | None
    hostname: str | None
    model_name: str | None
    overall_score: int
    classification: str
    categories: dict[str, CategoryScore]
    top_category: str | None
    contributing_factors: list[str]
    recommended_action: dict[str, Any] | None
    evidence: list[Evidence]


def _storage(fa: FleetAnalysis, did: str) -> CategoryScore:
    f = fa.storage.get(did)
    if not f:
        return CategoryScore(score=0, factors=["no storage telemetry"])
    st, cur, tr = fa.cfg.storage, f.current, f.trend or {}
    pct = cur["available_pct"]
    score, factors, kind = 0, [], "none"
    if pct < st.critical_pct:
        score, kind = 85, "confirmed_failure"
        factors.append(f"{pct}% free (< critical {st.critical_pct}%)")
    elif pct < st.low_pct:
        score, kind = 65, "confirmed_failure"
        factors.append(f"{pct}% free (< low {st.low_pct}%)")
    elif pct < st.warn_pct:
        score = 30
        factors.append(f"{pct}% free (< warn {st.warn_pct}%)")
    if tr.get("direction") == "deteriorating":
        score = min(100, max(score, 45) + 15)
        kind = "deteriorating"
        factors.append(f"declining {tr['slope_per_day']} pp/day over {tr['n']} snapshots")
        if cur.get("projected_days_to_critical") is not None:
            factors.append(f"projected to reach {st.critical_pct}% in ~{cur['projected_days_to_critical']} days")
    if tr.get("persistence") == "persistent":
        score = min(100, score + 10)
        kind = "persistent" if kind != "deteriorating" else kind
        factors.append(f"low in {tr['breach_count']}/{tr['n']} snapshots (persistent)")
    return CategoryScore(score=score, factors=factors, issue_type=kind)


def _memory(fa: FleetAnalysis, did: str) -> CategoryScore:
    f = fa.memory.get(did)
    if not f:
        return CategoryScore(score=0, factors=["no memory telemetry"])
    tr, mt = f.trend or {}, fa.cfg.memory
    p = tr.get("persistence")
    if p == "persistent":
        score = 70 + (15 if (tr.get("mean_value") or 0) >= mt.critical_pct else 0)
        return CategoryScore(score=score, issue_type="persistent", factors=[
            f">{mt.high_pct}% used in {tr['breach_count']}/{tr['n']} snapshots (mean {tr['mean_value']}%)"])
    if p == "intermittent":
        return CategoryScore(score=30, issue_type="intermittent",
                             factors=[f"intermittent: >{mt.high_pct}% in {tr['breach_count']}/{tr['n']} snapshots"])
    if p == "one_time":
        return CategoryScore(score=10, issue_type="one_time",
                             factors=[f"single spike to {tr['max_value']}% — not treated as a persistent problem"])
    if tr.get("direction") == "deteriorating":
        return CategoryScore(score=20, issue_type="deteriorating",
                             factors=[f"usage rising {tr['slope_per_day']} pp/day"])
    return CategoryScore(score=0)


def _battery(fa: FleetAnalysis, did: str) -> CategoryScore:
    f = fa.battery.get(did)
    if not f or f.pattern == "no_battery_reported":
        return CategoryScore(score=0, factors=["no battery reported (not applicable / unknown)"])
    cur, bt = f.current, fa.cfg.battery
    score, factors, kind = 0, [], "none"
    if cur["service_recommended"]:
        score, kind = 70, "confirmed_failure"
        factors.append(f"condition '{cur['condition']}'")
    if cur.get("high_cycle_count"):
        score = min(100, score + 15 if score else 25)
        factors.append(f"{cur['cycle_count']} cycles (>= {bt.high_cycle_count})")
    if cur.get("capacity_decline_pct") and cur["capacity_decline_pct"] >= bt.capacity_decline_pct:
        score = min(100, score + 10 if score else 30)
        kind = kind if kind != "none" else "deteriorating"
        factors.append(f"full-charge capacity down {cur['capacity_decline_pct']}% in window")
    if cur.get("latest_snapshot_missing_battery"):
        factors.append("latest snapshot missing battery block (telemetry gap, not a failure)")
    return CategoryScore(score=score, factors=factors, issue_type=kind)


def _compliance(fa: FleetAnalysis, did: str) -> CategoryScore:
    f = fa.compliance.get(did)
    if not f:
        return CategoryScore(score=0, factors=["no compliance telemetry"])
    score, factors, kind = 0, [], "none"
    for c in f.current["checks"]:
        if c["latest_status"] != "fail":
            continue
        kind = "confirmed_failure"
        if c["severity"] == "high":
            score = max(score, 90)
        else:
            score = min(60, score + 30)
        detail = "failing in all snapshots" if c["pattern"] == "persistent_failure" else (
            f"regressed pass->fail at {c['transitions'][-1]['collected_at']:%Y-%m-%d}" if c["pattern"] == "regressed" else "failing")
        factors.append(f"{c['check_id']} ({c['severity']}) {detail}")
        if c["pattern"] == "regressed":
            score = min(100, score + 10)
    return CategoryScore(score=score, factors=factors, issue_type=kind)


def _os(fa: FleetAnalysis, did: str) -> CategoryScore:
    f = fa.os.get(did)
    if not f:
        return CategoryScore(score=0)
    cur = f.current
    if cur["below_policy_minimum"]:
        return CategoryScore(score=50, issue_type="policy", factors=[
            f"{cur['os']} {cur['version']} below policy minimum {cur['os']} {cur['policy_min_major']}"])
    return CategoryScore(score=0)


def _recommend(fa: FleetAnalysis, did: str, cats: dict[str, CategoryScore], top: str | None) -> dict | None:
    if top is None or cats[top].score < fa.cfg.bands.watch:
        return None
    meta = fa.device_meta.get(did, {})
    apple_silicon = (meta.get("processor") or "").startswith("Apple")
    if top == "storage":
        return {"tool": "create_upgrade_order", "args": {"device_id": did, "component": "storage",
                "spec": "Increase internal/attached storage; clean up user data first"},
                "why": "storage below policy threshold or deteriorating"}
    if top == "memory":
        if apple_silicon:
            return {"tool": "flag_device_for_replacement", "args": {"device_id": did,
                    "reason": "Persistent memory pressure on a device with non-upgradeable unified memory"},
                    "why": "persistent RAM pressure; Apple-silicon memory cannot be upgraded"}
        return {"tool": "create_upgrade_order", "args": {"device_id": did, "component": "memory",
                "spec": "Upgrade RAM (e.g. to 32 GB)"}, "why": "persistent RAM pressure"}
    if top == "battery":
        return {"tool": "create_upgrade_order", "args": {"device_id": did, "component": "battery",
                "spec": "Replacement battery"}, "why": "battery service recommended / degraded"}
    if top == "compliance":
        f = fa.compliance[did]
        failing = [c for c in f.current["checks"] if c["latest_status"] == "fail"]
        failing.sort(key=lambda c: (c["severity"] != "high", c["pattern"] != "regressed"))
        return {"tool": "open_remediation_ticket", "args": {"device_id": did, "check_id": failing[0]["check_id"],
                "note": f"Remediate failing {failing[0]['check_id']} check"}, "why": "confirmed compliance failure"}
    if top == "os":
        return {"tool": "open_remediation_ticket", "args": {"device_id": did, "check_id": "os_up_to_date",
                "note": "Upgrade OS to a supported major version"}, "why": "OS below policy minimum"}
    return None


def score_device(fa: FleetAnalysis, did: str) -> DeviceRisk:
    cats = {"storage": _storage(fa, did), "memory": _memory(fa, did), "battery": _battery(fa, did),
            "compliance": _compliance(fa, did), "os": _os(fa, did)}
    w = fa.cfg.weights
    weighted = sum(cats[c].score * getattr(w, c) for c in CATEGORIES)
    mx = max(c.score for c in cats.values())
    overall = round(0.5 * mx + 0.5 * weighted)
    b = fa.cfg.bands
    cls = "critical" if overall >= b.critical else "at_risk" if overall >= b.at_risk else "watch" if overall >= b.watch else "healthy"
    top = max(CATEGORIES, key=lambda c: cats[c].score) if mx > 0 else None
    factors = [f"{c}: {f}" for c in CATEGORIES for f in cats[c].factors if cats[c].score > 0]
    evidence: list[Evidence] = []
    for c, src in (("storage", fa.storage), ("memory", fa.memory), ("battery", fa.battery),
                   ("compliance", fa.compliance), ("os", fa.os)):
        if cats[c].score > 0 and did in src:
            evidence.extend(src[did].evidence)
    meta = fa.device_meta.get(did, {})
    return DeviceRisk(
        device_id=did, employee_id=meta.get("employee_id"), hostname=meta.get("hostname"),
        model_name=meta.get("model_name"), overall_score=overall, classification=cls, categories=cats,
        top_category=top, contributing_factors=factors, recommended_action=_recommend(fa, did, cats, top),
        evidence=evidence,
    )


def score_fleet(fa: FleetAnalysis) -> list[DeviceRisk]:
    risks = [score_device(fa, did) for did in fa.device_meta]
    return sorted(risks, key=lambda r: (-r.overall_score, r.device_id))
