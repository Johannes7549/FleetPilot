"""Read-only tools. All statistics are computed deterministically from the tenant's telemetry."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Literal

from pydantic import Field, field_validator

from app.analytics.analyzers import DeviceFinding, parse_major
from app.analytics.insights import generate_insights
from app.analytics.risk import score_device, score_fleet
from app.schemas.evidence import ev
from app.tools.base import NoArgs, ToolContext, ToolInput, ToolOutput, tool

DEVICE_ID_PATTERN = r"^[A-Za-z0-9_\-]{4,64}$"


def _dump(findings: list[DeviceFinding], *, with_trend: bool = True) -> list[dict]:
    rows = []
    for f in findings:
        d = f.model_dump(mode="json", exclude={"evidence"})
        if not with_trend:
            d.pop("trend", None)
        d["evidence_ids"] = [e.evidence_id for e in f.evidence]
        rows.append(d)
    return rows


def _evidence(findings: list[DeviceFinding]):
    return [e for f in findings for e in f.evidence]


# ---------------------------------------------------------------------------------------------
@tool("get_fleet_overview",
      "Fleet-wide summary for the caller's company: device/employee/snapshot counts, observation window, "
      "platform and OS mix, risk classification counts, and latest compliance failure counts.",
      examples=("How many devices do we have?", "Give me a fleet overview"))
def get_fleet_overview(ctx: ToolContext, _: NoArgs) -> ToolOutput:
    fa, repo = ctx.analysis, ctx.repo
    company = repo.company()
    df = fa.snapshots
    if df.empty:
        return ToolOutput(summary=f"No telemetry has been ingested for {company.name}.",
                          data={"company": company.name, "devices": 0})
    latest = df.sort_values("collected_at").groupby("device_id").tail(1)
    risks = score_fleet(fa)
    risk_counts = Counter(r.classification for r in risks)
    fail_counts = Counter(c["check_id"] for f in fa.compliance.values() for c in f.current["checks"]
                          if c["latest_status"] == "fail")
    os_counts = Counter(f"{r.os_product_name} {r.os_product_version}" for r in latest.itertuples())
    data = {
        "company": company.name,
        "devices": int(latest.shape[0]),
        "employees": len(repo.employees()),
        "snapshots": int(df.shape[0]),
        "window_start": df["collected_at"].min().isoformat(),
        "window_end": df["collected_at"].max().isoformat(),
        "platforms": dict(Counter(latest["os_platform"])),
        "os_versions": dict(sorted(os_counts.items())),
        "risk_classification": {k: risk_counts.get(k, 0) for k in ("critical", "at_risk", "watch", "healthy")},
        "latest_compliance_failures_by_check": dict(fail_counts),
        "devices_with_any_compliance_failure": sum(1 for f in fa.compliance.values() if f.flagged),
    }
    evidence = [ev(company_id=ctx.tenant.company_id, metric="device_count", observed_value=data["devices"],
                   source="devices", temporal_scope="latest", note="distinct devices with telemetry"),
                ev(company_id=ctx.tenant.company_id, metric="snapshot_count", observed_value=data["snapshots"],
                   source="device_snapshots", temporal_scope="window_aggregate",
                   note=f"{data['window_start']} to {data['window_end']}")]
    for r in latest.itertuples():
        evidence.append(ev(company_id=ctx.tenant.company_id, device_id=r.device_id, snapshot_id=r.id,
                           collected_at=r.collected_at.to_pydatetime(), metric="latest_snapshot",
                           observed_value=f"{r.os_product_name} {r.os_product_version}"))
    summary = (f"{company.name}: {data['devices']} devices, {data['employees']} employees, {data['snapshots']} "
               f"snapshots ({data['window_start'][:10]} to {data['window_end'][:10]}). Risk: "
               + ", ".join(f"{v} {k}" for k, v in data["risk_classification"].items()) + ".")
    return ToolOutput(summary=summary, data=data, evidence=evidence)


# ---------------------------------------------------------------------------------------------
class DeviceArgs(ToolInput):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN, description="Device ID (serial), e.g. 8NM23J95R5I6")


@tool("get_device_details",
      "Inventory details, latest snapshot measurements and risk score for one device in the caller's company.",
      DeviceArgs, examples=("Tell me about device 8NM23J95R5I6",))
def get_device_details(ctx: ToolContext, a: DeviceArgs) -> ToolOutput:
    dev = ctx.repo.device(a.device_id)
    snap = ctx.repo.snapshot(dev.latest_snapshot_id) if dev.latest_snapshot_id else None
    risk = score_device(ctx.analysis, dev.id)
    data = {
        "device": {"device_id": dev.id, "employee_id": dev.employee_id, "hostname": dev.hostname,
                   "model_name": dev.model_name, "model_identifier": dev.model_identifier, "processor": dev.processor,
                   "total_memory": dev.total_memory_label, "platform": dev.platform,
                   "first_seen_at": dev.first_seen_at, "last_seen_at": dev.last_seen_at},
        "latest_snapshot": None if snap is None else {
            "snapshot_id": snap.id, "collected_at": snap.collected_at,
            "os": f"{snap.os_product_name} {snap.os_product_version} ({snap.os_build_version})",
            "memory_used_pct": snap.memory_used_pct, "disk_available_pct": snap.primary_disk_available_pct,
            "battery": None if not snap.battery_reported else {
                "condition": snap.battery_condition, "cycle_count": snap.battery_cycle_count,
                "percentage": snap.battery_percentage, "full_charge_capacity": snap.battery_full_charge_capacity},
            "battery_reported": snap.battery_reported,
            "network_reported": snap.network_reported,
            "installed_software": [s.name for s in snap.installed_software],
            "compliance": [{"check_id": c.check_id, "status": c.status, "severity": c.severity}
                           for c in snap.compliance_results],
            "volumes": [{"name": v.volume_name, "mount": v.mount_point, "encrypted": v.encrypted,
                         "size_gb": round((v.size_bytes or 0) / 1024**3, 1),
                         "available_gb": round((v.available_bytes or 0) / 1024**3, 1)} for v in snap.disk_volumes],
        },
        "risk": risk.model_dump(mode="json", exclude={"evidence"}),
    }
    evidence = list(risk.evidence)
    if snap:
        evidence.insert(0, ev(company_id=ctx.tenant.company_id, device_id=dev.id, snapshot_id=snap.id,
                              collected_at=snap.collected_at, metric="latest_snapshot",
                              observed_value=f"{snap.os_product_name} {snap.os_product_version}"))
    summary = (f"{dev.id} ({dev.model_name}, {dev.hostname}) owned by {dev.employee_id}; risk {risk.overall_score}/100 "
               f"({risk.classification})" + (f"; top issue: {risk.top_category}" if risk.top_category else "") + ".")
    return ToolOutput(summary=summary, data=data, evidence=evidence)


# ---------------------------------------------------------------------------------------------
class StorageArgs(ToolInput):
    threshold_pct: float | None = Field(None, gt=0, lt=100, description="Available-% threshold; default is policy low_pct")
    include_deteriorating: bool = Field(True, description="Also list devices above threshold but trending down")


@tool("find_low_storage_devices",
      "Devices whose latest snapshot is below an available-disk threshold, with 30-day trend "
      "(persistent / deteriorating) and projected days to critical.", StorageArgs,
      examples=("Which devices are low on disk space?",))
def find_low_storage_devices(ctx: ToolContext, a: StorageArgs) -> ToolOutput:
    fa = ctx.analysis
    thr = a.threshold_pct or fa.cfg.storage.low_pct
    findings = list(fa.storage.values())
    low = [f.model_copy(deep=True) for f in findings if f.current["available_pct"] < thr]
    low.sort(key=lambda f: f.current["available_pct"])
    trending = [f for f in findings if f.current["available_pct"] >= thr and f.trend and f.trend["direction"] == "deteriorating"] \
        if a.include_deteriorating else []
    for f in low:  # restate evidence against the threshold actually applied
        f.evidence[0] = f.evidence[0].model_copy(update={"threshold": thr})
    summary = (f"{len(low)} of {len(findings)} devices are below {thr}% free disk in their latest snapshot"
               + (": " + ", ".join(f"{f.device_id} {f.current['available_pct']}% ({f.pattern})" for f in low) if low else "")
               + "." + (f" {len(trending)} more are trending down." if trending else ""))
    return ToolOutput(summary=summary, data={"threshold_pct": thr, "devices_evaluated": len(findings),
                                             "low_storage_devices": _dump(low), "deteriorating_above_threshold": _dump(trending)},
                      evidence=_evidence(low + trending))


# ---------------------------------------------------------------------------------------------
class MemoryArgs(ToolInput):
    threshold_pct: float | None = Field(None, gt=0, le=100, description="Used-% threshold; default policy high_pct")
    only_flagged: bool = Field(True, description="Only devices exceeding the threshold at least once")


@tool("analyze_memory_usage",
      "RAM utilisation per device across all snapshots; classifies persistent pressure vs one-time spikes "
      "vs intermittent, with mean/peak usage.", MemoryArgs,
      examples=("Which devices have consistently high RAM usage?",))
def analyze_memory_usage(ctx: ToolContext, a: MemoryArgs) -> ToolOutput:
    fa = ctx.analysis
    cfg = fa.cfg
    if a.threshold_pct and a.threshold_pct != cfg.memory.high_pct:
        from app.analytics.analyzers import analyze_memory
        cfg = cfg.model_copy(update={"memory": cfg.memory.model_copy(update={"high_pct": a.threshold_pct})})
        findings = analyze_memory(fa.snapshots, cfg, ctx.tenant.company_id)
    else:
        findings = list(fa.memory.values())
    thr = cfg.memory.high_pct
    rows = [f for f in findings if f.trend and f.trend["breach_count"] > 0] if a.only_flagged else findings
    order = {"persistent": 0, "intermittent": 1, "one_time": 2}
    rows.sort(key=lambda f: (order.get(f.trend["persistence"], 3), -(f.trend["mean_value"] or 0)))
    persistent = [f for f in rows if f.trend["persistence"] == "persistent"]
    summary = (f"{len(persistent)} device(s) show persistent RAM pressure (>{thr}% used in >= "
               f"{int(cfg.trends.persistent_ratio * 100)}% of snapshots)"
               + (": " + ", ".join(f"{f.device_id} ({f.trend['breach_count']}/{f.trend['n']}, mean {f.trend['mean_value']}%)" for f in persistent) if persistent else "")
               + f". {len(rows) - len(persistent)} other device(s) exceeded {thr}% only occasionally.")
    return ToolOutput(summary=summary, data={"threshold_pct": thr, "persistent_ratio": cfg.trends.persistent_ratio,
                                             "devices": _dump(rows), "persistent_count": len(persistent)},
                      evidence=_evidence(rows))


# ---------------------------------------------------------------------------------------------
class BatteryArgs(ToolInput):
    only_flagged: bool = Field(True, description="Only devices needing service or with declining capacity")


@tool("analyze_battery_health",
      "Battery condition, cycle count, full-charge-capacity change and condition transitions per device; "
      "devices without battery telemetry are reported as unknown, not failing.", BatteryArgs,
      examples=("Which employees have devices with battery service recommendations?",))
def analyze_battery_health(ctx: ToolContext, a: BatteryArgs) -> ToolOutput:
    findings = list(ctx.analysis.battery.values())
    rows = [f for f in findings if f.flagged] if a.only_flagged else findings
    svc = [f for f in rows if f.current.get("service_recommended")]
    no_batt = [f.device_id for f in findings if f.pattern == "no_battery_reported"]
    gaps = [f.device_id for f in findings if f.current.get("latest_snapshot_missing_battery")]
    summary = (f"{len(svc)} device(s) report battery condition requiring service"
               + (": " + ", ".join(f"{f.device_id} (owner {f.employee_id}, {f.current['condition']}, "
                                   f"{f.current['cycle_count']} cycles)" for f in svc) if svc else "")
               + f". {len(no_batt)} device(s) report no battery at all (not applicable/unknown)."
               + (f" Latest snapshot lacks battery data for: {', '.join(gaps)} (status unknown, not a failure)." if gaps else ""))
    return ToolOutput(summary=summary, data={"devices": _dump(rows), "service_recommended_count": len(svc),
                                             "employees_affected": sorted({f.employee_id for f in svc if f.employee_id}),
                                             "no_battery_reported": no_batt, "latest_battery_missing": gaps},
                      evidence=_evidence(rows))


# ---------------------------------------------------------------------------------------------
class ComplianceArgs(ToolInput):
    check_id: str | None = Field(None, pattern=r"^[a-z0-9_]{2,64}$", description="e.g. disk_encryption, screen_lock, os_up_to_date")
    severity: Literal["high", "medium", "low"] | None = None
    status: Literal["fail", "pass", "any"] = "fail"


@tool("get_compliance_status",
      "Latest compliance check results per device, filterable by check_id, severity and status.", ComplianceArgs,
      examples=("Show me devices failing high-severity compliance checks",))
def get_compliance_status(ctx: ToolContext, a: ComplianceArgs) -> ToolOutput:
    fa = ctx.analysis
    known_checks = sorted({c["check_id"] for f in fa.compliance.values() for c in f.current["checks"]})
    rows, evidence = [], []
    for f in fa.compliance.values():
        for c in f.current["checks"]:
            if a.check_id and c["check_id"] != a.check_id:
                continue
            if a.severity and c["severity"] != a.severity:
                continue
            if a.status != "any" and c["latest_status"] != a.status:
                continue
            rows.append({"device_id": f.device_id, "employee_id": f.employee_id, "hostname": f.hostname,
                         **{k: v for k, v in c.items() if k != "transitions"},
                         "latest_collected_at": f.latest_collected_at})
            evidence.extend(e for e in f.evidence if e.metric == f"compliance:{c['check_id']}")
    filt = ", ".join(x for x in [a.check_id and f"check={a.check_id}", a.severity and f"severity={a.severity}",
                                 f"status={a.status}"] if x)
    devices = sorted({r["device_id"] for r in rows})
    note = ""
    if a.check_id and a.check_id not in known_checks:
        note = f" Check '{a.check_id}' does not exist in the telemetry (known checks: {', '.join(known_checks)})."
    summary = (f"{len(rows)} result(s) on {len(devices)} device(s) match [{filt}] in the latest snapshots"
               + (": " + ", ".join(devices) if 0 < len(devices) <= 25 else "") + "." + note)
    return ToolOutput(summary=summary, data={"filters": a.model_dump(), "known_checks": known_checks,
                                             "results": rows, "device_count": len(devices)}, evidence=evidence)


# ---------------------------------------------------------------------------------------------
class ComplianceTrendArgs(ToolInput):
    check_id: str | None = Field(None, pattern=r"^[a-z0-9_]{2,64}$")


@tool("analyze_compliance_trends",
      "Compliance drift over the window: pass->fail regressions (with dates), recoveries and persistent failures.",
      ComplianceTrendArgs, examples=("Is compliance getting worse over time?",))
def analyze_compliance_trends(ctx: ToolContext, a: ComplianceTrendArgs) -> ToolOutput:
    fa = ctx.analysis
    cdf = ctx.repo.compliance_frame()
    by_pattern: dict[str, list] = {"regressed": [], "recovered": [], "persistent_failure": [], "failing": []}
    evidence = []
    for f in fa.compliance.values():
        for c in f.current["checks"]:
            if a.check_id and c["check_id"] != a.check_id:
                continue
            if c["pattern"] in by_pattern:
                by_pattern[c["pattern"]].append({"device_id": f.device_id, "check_id": c["check_id"],
                                                 "severity": c["severity"], "transitions": c["transitions"],
                                                 "fail_count": c["fail_count"], "snapshots": c["snapshots"]})
                evidence.extend(e for e in f.evidence if e.metric.endswith(c["check_id"]))
    daily = []
    if not cdf.empty:
        sub = cdf if not a.check_id else cdf[cdf["check_id"] == a.check_id]
        sub = sub.assign(day=sub["collected_at"].dt.strftime("%Y-%m-%d"))
        g = sub.groupby("day")["status"].apply(lambda s: round((s == "fail").mean() * 100, 1))
        daily = [{"day": d, "fail_rate_pct": v} for d, v in g.items()]
    first = daily[0]["fail_rate_pct"] if daily else None
    last = daily[-1]["fail_rate_pct"] if daily else None
    summary = (f"Check failure rate moved from {first}% to {last}% of results over the window. "
               f"{len(by_pattern['regressed'])} regression(s) (pass->fail): "
               + (", ".join(f"{r['device_id']} {r['check_id']} on {r['transitions'][-1]['collected_at']:%Y-%m-%d}"
                            for r in by_pattern["regressed"]) or "none")
               + f"; {len(by_pattern['persistent_failure'])} failing in every snapshot; "
               f"{len(by_pattern['recovered'])} recovered.")
    return ToolOutput(summary=summary, data={"check_id": a.check_id, "daily_fail_rate": daily, **by_pattern},
                      evidence=evidence)


# ---------------------------------------------------------------------------------------------
class OSArgs(ToolInput):
    os_name: Literal["macOS", "Windows"] | None = Field(None, description="Restrict to one OS family")
    older_than_major: int | None = Field(None, ge=1, le=100, description="List devices whose major version is below this")


@tool("get_os_distribution",
      "OS family/version distribution from latest snapshots, version changes over the window, and devices "
      "older than a given major version.", OSArgs,
      examples=("How many devices are running an OS older than macOS 15?",))
def get_os_distribution(ctx: ToolContext, a: OSArgs) -> ToolOutput:
    fa = ctx.analysis
    rows = [f for f in fa.os.values() if not a.os_name or f.current["os"] == a.os_name]
    dist = Counter(f"{f.current['os']} {f.current['version']}" for f in rows)
    older = []
    if a.older_than_major is not None:
        older = [f.model_copy(deep=True) for f in rows if f.current["major"] is not None and f.current["major"] < a.older_than_major]
        for f in older:
            f.evidence[0] = f.evidence[0].model_copy(update={"threshold": f"{f.current['os']} >= {a.older_than_major}",
                                                             "comparator": "<"})
    changes = [{"device_id": f.device_id, **c} for f in rows for c in f.current["version_changes"]]
    label = f"{a.os_name or 'OS'} {a.older_than_major}"
    summary = (f"OS distribution ({len(rows)} devices): " + ", ".join(f"{k}: {v}" for k, v in sorted(dist.items())) + "."
               + (f" {len(older)} device(s) run a version older than {label}: "
                  + ", ".join(f"{f.device_id} ({f.current['version']})" for f in older) + "." if a.older_than_major is not None else "")
               + (f" {len(changes)} OS version change(s) observed." if changes else " No OS version changes observed in the window."))
    return ToolOutput(summary=summary, data={"distribution": dict(dist), "devices_older": _dump(older, with_trend=False),
                                             "older_count": len(older), "version_changes": changes,
                                             "filter": a.model_dump()},
                      evidence=[e for f in (older or rows) for e in f.evidence])


# ---------------------------------------------------------------------------------------------
class SoftwareArgs(ToolInput):
    name: str | None = Field(None, min_length=2, max_length=100, description="Software name substring")
    device_id: str | None = Field(None, pattern=DEVICE_ID_PATTERN)
    watchlist_only: bool = False


@tool("get_installed_software",
      "Installed software from latest snapshots (optionally by name or device), install counts, and when "
      "watch-listed packages first appeared.", SoftwareArgs,
      examples=("Which devices have uTorrent installed?",))
def get_installed_software(ctx: ToolContext, a: SoftwareArgs) -> ToolOutput:
    if a.device_id:
        ctx.repo.device(a.device_id)
    sdf = ctx.repo.software_frame(a.device_id)
    if sdf.empty:
        return ToolOutput(summary="No installed-software telemetry found.", data={"packages": []})
    latest_t = sdf.groupby("device_id")["collected_at"].transform("max")
    latest = sdf[sdf["collected_at"] == latest_t]
    if a.name:
        latest = latest[latest["name"].str.contains(a.name, case=False, regex=False)]
    if a.watchlist_only:
        wl = {w.lower() for w in ctx.analysis.cfg.software_watchlist}
        latest = latest[latest["name"].str.lower().isin(wl)]
    packages = []
    for (name, version, publisher), g in latest.groupby(["name", "version", "publisher"], dropna=False):
        packages.append({"name": name, "version": version, "publisher": publisher,
                         "device_count": int(g["device_id"].nunique()), "device_ids": sorted(g["device_id"].unique())})
    packages.sort(key=lambda p: (-p["device_count"], p["name"]))
    watch = [f.model_dump(mode="json", exclude={"evidence"}) for f in ctx.analysis.software.values()
             if not a.device_id or f.device_id == a.device_id]
    evidence = [ev(company_id=ctx.tenant.company_id, device_id=r.device_id, snapshot_id=r.snapshot_id,
                   collected_at=r.collected_at.to_pydatetime(), metric="installed_software",
                   observed_value=f"{r.name} {r.version}", source="installed_software")
                for r in latest.itertuples()] if a.name or a.device_id or a.watchlist_only else []
    evidence += [e for f in ctx.analysis.software.values() for e in f.evidence
                 if (not a.device_id or f.device_id == a.device_id)
                 and (not a.name or a.name.lower() in str(e.observed_value).lower())]
    summary = (f"{len(packages)} package/version combination(s) found"
               + (f" matching '{a.name}'" if a.name else "") + (f" on {a.device_id}" if a.device_id else "") + ": "
               + ", ".join(f"{p['name']} {p['version']} ({p['device_count']} device(s)"
                           + (": " + ", ".join(p["device_ids"]) if (a.name or a.watchlist_only) and p["device_count"] <= 25 else "")
                           + ")" for p in packages[:12]) + ".")
    return ToolOutput(summary=summary, data={"packages": packages, "watchlisted": watch}, evidence=evidence)


# ---------------------------------------------------------------------------------------------
class InsightArgs(ToolInput):
    category: Literal["storage", "memory", "battery", "compliance", "os", "software", "data_quality"] | None = None
    top_devices: int = Field(5, ge=0, le=50, description="How many highest-risk devices to include")


@tool("generate_fleet_insights",
      "Fleet insights (persistent problems, deteriorating trends, compliance drift, policy gaps) with "
      "severity, evidence and recommended next steps, plus the highest-risk devices.", InsightArgs,
      examples=("What are the biggest fleet health risks this month?",))
def generate_fleet_insights(ctx: ToolContext, a: InsightArgs) -> ToolOutput:
    insights = generate_insights(ctx.analysis)
    if a.category:
        insights = [i for i in insights if i.category == a.category]
    risks = score_fleet(ctx.analysis)[: a.top_devices]
    evidence = [e for i in insights for e in i.evidence]
    evidence += [e for r in risks for e in r.evidence]
    summary = (f"{len(insights)} insight(s): " + "; ".join(f"[{i.severity}] {i.title}" for i in insights)
               + (". Highest-risk devices: " + ", ".join(f"{r.device_id} {r.overall_score} ({r.classification})" for r in risks) if risks else "") + ".")
    return ToolOutput(summary=summary, data={
        "insights": [i.model_dump(mode="json", exclude={"evidence"}) | {"evidence_ids": [e.evidence_id for e in i.evidence]} for i in insights],
        "top_risk_devices": [r.model_dump(mode="json", exclude={"evidence"}) for r in risks]}, evidence=evidence)


# ---------------------------------------------------------------------------------------------
class RiskArgs(ToolInput):
    classification: Literal["critical", "at_risk", "watch", "healthy"] | None = None
    limit: int = Field(25, ge=1, le=200)


@tool("get_risk_scores",
      "Deterministic per-device risk scores (0-100) with category breakdown, contributing factors and the "
      "recommended action.", RiskArgs, examples=("Which devices are critical?",))
def get_risk_scores(ctx: ToolContext, a: RiskArgs) -> ToolOutput:
    risks = score_fleet(ctx.analysis)
    if a.classification:
        risks = [r for r in risks if r.classification == a.classification]
    risks = risks[: a.limit]
    summary = (f"{len(risks)} device(s)" + (f" classified {a.classification}" if a.classification else "") + ": "
               + ", ".join(f"{r.device_id} {r.overall_score} ({r.classification})" for r in risks) + ".")
    return ToolOutput(summary=summary, data={"devices": [r.model_dump(mode="json", exclude={"evidence"}) for r in risks]},
                      evidence=[e for r in risks for e in r.evidence])


# ---------------------------------------------------------------------------------------------
class HistoryArgs(ToolInput):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    metric: Literal["storage", "memory", "battery", "compliance", "os", "all"] = "all"
    days: int | None = Field(None, ge=1, le=365, description="Only the last N days of the window")

    @field_validator("metric", mode="before")
    @classmethod
    def _norm(cls, v):
        aliases = {"disk": "storage", "ram": "memory"}
        return aliases.get(v, v) if isinstance(v, str) else v


@tool("get_device_history",
      "Full time series for one device (disk %, RAM %, battery condition/cycles, compliance, OS) with trend "
      "classification.", HistoryArgs, examples=("Show the history of 8NM23J95R5I6",))
def get_device_history(ctx: ToolContext, a: HistoryArgs) -> ToolOutput:
    snaps = ctx.repo.device_snapshots(a.device_id)
    if a.days and snaps:
        cutoff = snaps[-1].collected_at - timedelta(days=a.days)
        snaps = [s for s in snaps if s.collected_at >= cutoff]
    if not snaps:
        return ToolOutput(summary=f"No snapshots found for {a.device_id}.", data={"series": []})
    m = a.metric
    series = []
    for s in snaps:
        row = {"snapshot_id": s.id, "collected_at": s.collected_at}
        if m in ("storage", "all"):
            row["disk_available_pct"] = s.primary_disk_available_pct
        if m in ("memory", "all"):
            row["memory_used_pct"] = s.memory_used_pct
        if m in ("battery", "all"):
            row.update(battery_reported=s.battery_reported, battery_condition=s.battery_condition,
                       battery_cycle_count=s.battery_cycle_count, battery_full_charge_capacity=s.battery_full_charge_capacity)
        if m in ("compliance", "all"):
            row["compliance"] = {c.check_id: c.status for c in s.compliance_results}
        if m in ("os", "all"):
            row["os_version"] = f"{s.os_product_name} {s.os_product_version}"
        series.append(row)
    fa = ctx.analysis
    trends, evidence = {}, []
    for key, src in (("storage", fa.storage), ("memory", fa.memory), ("battery", fa.battery),
                     ("compliance", fa.compliance), ("os", fa.os)):
        if m in (key, "all") and a.device_id in src:
            f = src[a.device_id]
            trends[key] = {"pattern": f.pattern, "trend": f.trend, "current": f.current}
            evidence.extend(f.evidence)
    summary = (f"{len(series)} snapshot(s) for {a.device_id} from {snaps[0].collected_at:%Y-%m-%d} to "
               f"{snaps[-1].collected_at:%Y-%m-%d}. Patterns: "
               + ", ".join(f"{k}={v['pattern']}" for k, v in trends.items()) + ".")
    return ToolOutput(summary=summary, data={"device_id": a.device_id, "metric": m, "series": series, "trends": trends},
                      evidence=evidence)


# ---------------------------------------------------------------------------------------------
class EmployeeArgs(ToolInput):
    employee_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{2,64}$")


@tool("get_employee_devices",
      "Devices assigned to one employee in the caller's company, with each device's risk classification.",
      EmployeeArgs, examples=("Which devices does emp-acme-1003 have?",))
def get_employee_devices(ctx: ToolContext, a: EmployeeArgs) -> ToolOutput:
    ctx.repo.employee(a.employee_id)
    devs = [d for d in ctx.repo.devices() if d.employee_id == a.employee_id]
    risks = [score_device(ctx.analysis, d.id) for d in devs]
    summary = f"{a.employee_id} has {len(devs)} device(s): " + ", ".join(
        f"{r.device_id} ({r.model_name}, risk {r.overall_score} {r.classification})" for r in risks) + "."
    return ToolOutput(summary=summary, data={"employee_id": a.employee_id,
                                             "devices": [r.model_dump(mode="json", exclude={"evidence"}) for r in risks]},
                      evidence=[e for r in risks for e in r.evidence])


__all__ = ["parse_major"]
