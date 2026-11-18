"""Dashboard metrics — computed from the tenant's telemetry on every request (no mock numbers)."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select

from app.analytics.fleet import FleetAnalysis
from app.analytics.risk import score_fleet
from app.models import ActionProposal
from app.services.actions import ActionService


def _bucket(value: float, edges: list[tuple[float, str]], default: str) -> str:
    for edge, label in edges:
        if value < edge:
            return label
    return default


def build_dashboard(fa: FleetAnalysis, actions: ActionService) -> dict:
    repo, cfg = fa.repo, fa.cfg
    df = fa.snapshots
    company = repo.company()
    if df.empty:
        return {"company": {"id": company.id, "name": company.name}, "empty": True}
    risks = score_fleet(fa)
    risk_counts = Counter(r.classification for r in risks)

    latest = df.sort_values("collected_at").groupby("device_id").tail(1)
    st, mt = cfg.storage, cfg.memory
    storage = Counter(_bucket(v, [(st.critical_pct, "critical"), (st.low_pct, "low"), (st.warn_pct, "warning")], "healthy")
                      for v in latest["primary_disk_available_pct"])
    memory = Counter("persistent_pressure" if f.trend["persistence"] == "persistent" else
                     "occasional_spikes" if f.trend["persistence"] in ("one_time", "intermittent") else "healthy"
                     for f in fa.memory.values())
    battery = Counter("service_recommended" if f.current.get("service_recommended") else
                      "no_battery" if f.pattern == "no_battery_reported" else
                      "unknown_latest" if f.current.get("latest_snapshot_missing_battery") else "normal"
                      for f in fa.battery.values())
    checks: dict[str, Counter] = {}
    for f in fa.compliance.values():
        for c in f.current["checks"]:
            checks.setdefault(c["check_id"], Counter())[c["latest_status"]] += 1
    compliance = [{"check_id": k, "severity": next(c["severity"] for f in fa.compliance.values()
                                                     for c in f.current["checks"] if c["check_id"] == k),
                   "pass": v.get("pass", 0), "fail": v.get("fail", 0)} for k, v in sorted(checks.items())]
    os_dist = Counter(f"{r.os_product_name} {r.os_product_version}" for r in latest.itertuples())

    d = df.assign(day=df["collected_at"].dt.strftime("%Y-%m-%d"))
    cdf = repo.compliance_frame()
    cday = (cdf.assign(day=cdf["collected_at"].dt.strftime("%Y-%m-%d")).groupby("day")["status"]
            .apply(lambda s: round((s == "fail").mean() * 100, 1))) if not cdf.empty else {}
    trends = []
    for day, g in d.groupby("day"):
        trends.append({
            "day": day,
            "avg_disk_available_pct": round(float(g["primary_disk_available_pct"].mean()), 2),
            "avg_memory_used_pct": round(float(g["memory_used_pct"].mean()), 2),
            "devices_low_storage": int((g["primary_disk_available_pct"] < st.low_pct).sum()),
            "devices_high_memory": int((g["memory_used_pct"] > mt.high_pct).sum()),
            "battery_service_recommended": int(g["battery_condition"].isin(cfg.battery.service_conditions).sum()),
            "compliance_fail_rate_pct": float(cday.get(day, 0)) if len(cday) else 0.0,
        })
    pending = repo.s.scalar(select(func.count()).select_from(ActionProposal).where(
        ActionProposal.company_id == repo.cid, ActionProposal.status == "pending_approval"))
    actions.expire_stale()
    return {
        "company": {"id": company.id, "name": company.name},
        "generated_from": {"snapshots": int(len(df)), "window_start": df["collected_at"].min().isoformat(),
                           "window_end": df["collected_at"].max().isoformat()},
        "totals": {"devices": len(risks), "employees": len(repo.employees()),
                   "healthy": risk_counts.get("healthy", 0), "watch": risk_counts.get("watch", 0),
                   "at_risk": risk_counts.get("at_risk", 0), "critical": risk_counts.get("critical", 0),
                   "pending_approvals": int(pending or 0)},
        "storage_health": dict(storage), "memory_health": dict(memory), "battery_health": dict(battery),
        "compliance": compliance, "os_distribution": dict(sorted(os_dist.items())),
        "trends": trends,
        "top_risks": [r.model_dump(mode="json", exclude={"evidence"}) for r in risks[:8]],
        "thresholds": {"low_storage_pct": st.low_pct, "critical_storage_pct": st.critical_pct,
                       "memory_high_pct": mt.high_pct},
    }
