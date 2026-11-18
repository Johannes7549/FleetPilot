"""Per-domain deterministic analyzers over a tenant's snapshot history.

Each analyzer returns one finding per device with: current (latest snapshot) values, a trend
summary across the full window, and Evidence objects pointing at the exact snapshots used.
All numbers are computed here (SQL + pandas); the LLM never computes statistics.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from app.analytics.config import RiskConfig
from app.analytics.trends import TrendSummary, classify
from app.schemas.evidence import Evidence, ev

GB = 1024**3


def _ts(x: Any):
    return x.to_pydatetime() if hasattr(x, "to_pydatetime") else x


def parse_major(version: str | None) -> int | None:
    if not version:
        return None
    m = re.match(r"\s*(\d+)", version)
    return int(m.group(1)) if m else None


class DeviceFinding(BaseModel):
    device_id: str
    employee_id: str | None = None
    hostname: str | None = None
    latest_snapshot_id: str | None = None
    latest_collected_at: Any = None
    current: dict[str, Any] = Field(default_factory=dict)
    trend: dict[str, Any] | None = None
    pattern: str = "normal"
    flagged: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


def _groups(df: pd.DataFrame):
    for device_id, g in df.groupby("device_id", sort=True):
        yield device_id, g.sort_values("collected_at")


# ----------------------------------------------------------------------------------------------
def analyze_storage(df: pd.DataFrame, cfg: RiskConfig, company_id: str) -> list[DeviceFinding]:
    st = cfg.storage
    out: list[DeviceFinding] = []
    for device_id, g in _groups(df):
        last, first = g.iloc[-1], g.iloc[0]
        trend: TrendSummary = classify(
            list(g["collected_at"]), list(g["primary_disk_available_pct"]),
            lambda v: v < st.low_pct, cfg.trends, bad_slope=st.deteriorating_slope_pp_per_day, higher_is_worse=False,
        )
        latest_pct = float(last["primary_disk_available_pct"])
        projected = None
        if trend.slope_per_day and trend.slope_per_day < 0 and latest_pct > st.critical_pct:
            projected = round((latest_pct - st.critical_pct) / -trend.slope_per_day, 1)
        f = DeviceFinding(
            device_id=device_id, employee_id=last["employee_id"], hostname=last["hostname"],
            latest_snapshot_id=last["id"], latest_collected_at=_ts(last["collected_at"]),
            current={
                "available_pct": round(latest_pct, 2),
                "available_gb": round(last["primary_disk_available_bytes"] / GB, 1),
                "size_gb": round(last["primary_disk_size_bytes"] / GB, 1),
                "below_low_threshold": latest_pct < st.low_pct,
                "below_critical_threshold": latest_pct < st.critical_pct,
                "projected_days_to_critical": projected,
            },
            trend=trend.as_dict(), pattern=trend.label,
            flagged=latest_pct < st.low_pct or trend.direction == "deteriorating" and latest_pct < st.warn_pct,
        )
        f.evidence = [
            ev(company_id=company_id, device_id=device_id, snapshot_id=last["id"],
               collected_at=_ts(last["collected_at"]), metric="primary_disk_available_pct",
               observed_value=round(latest_pct, 2), threshold=st.low_pct, comparator="<", unit="%",
               note=f"{last['primary_disk_available_bytes'] / GB:.1f} GB free of {last['primary_disk_size_bytes'] / GB:.1f} GB"),
            ev(company_id=company_id, device_id=device_id, snapshot_id=first["id"],
               collected_at=_ts(first["collected_at"]), metric="primary_disk_available_pct",
               observed_value=round(float(first["primary_disk_available_pct"]), 2), unit="%",
               temporal_scope="historical", note="earliest snapshot in window"),
            ev(company_id=company_id, device_id=device_id, snapshot_id=last["id"],
               collected_at=_ts(last["collected_at"]), metric="disk_low_snapshot_count",
               observed_value=f"{trend.breach_count}/{trend.n}", threshold=st.low_pct, comparator="<",
               temporal_scope="window_aggregate",
               note=f"slope {trend.slope_per_day} pp/day; pattern {trend.label}"),
        ]
        out.append(f)
    return out


# ----------------------------------------------------------------------------------------------
def analyze_memory(df: pd.DataFrame, cfg: RiskConfig, company_id: str) -> list[DeviceFinding]:
    mt = cfg.memory
    out: list[DeviceFinding] = []
    for device_id, g in _groups(df):
        last = g.iloc[-1]
        trend = classify(
            list(g["collected_at"]), list(g["memory_used_pct"]), lambda v: v > mt.high_pct, cfg.trends,
            bad_slope=mt.rising_slope_pp_per_day, higher_is_worse=True,
        )
        peak = g.loc[g["memory_used_pct"].idxmax()]
        f = DeviceFinding(
            device_id=device_id, employee_id=last["employee_id"], hostname=last["hostname"],
            latest_snapshot_id=last["id"], latest_collected_at=_ts(last["collected_at"]),
            current={
                "used_pct": round(float(last["memory_used_pct"]), 2),
                "total_gb": round(last["memory_total_bytes"] / GB, 1),
                "mean_used_pct": trend.mean_value,
                "peak_used_pct": trend.max_value,
                "snapshots_above_threshold": trend.breach_count,
                "snapshots_total": trend.n,
            },
            trend=trend.as_dict(), pattern=trend.label,
            flagged=trend.persistence in {"persistent", "intermittent"},
        )
        f.evidence = [
            ev(company_id=company_id, device_id=device_id, snapshot_id=last["id"],
               collected_at=_ts(last["collected_at"]), metric="memory_used_pct",
               observed_value=round(float(last["memory_used_pct"]), 2), threshold=mt.high_pct, comparator=">",
               unit="%"),
            ev(company_id=company_id, device_id=device_id, snapshot_id=peak["id"],
               collected_at=_ts(peak["collected_at"]), metric="memory_used_pct_peak",
               observed_value=round(float(peak["memory_used_pct"]), 2), threshold=mt.high_pct, comparator=">",
               unit="%", temporal_scope="historical"),
            ev(company_id=company_id, device_id=device_id, snapshot_id=last["id"],
               collected_at=_ts(last["collected_at"]), metric="memory_high_snapshot_ratio",
               observed_value=f"{trend.breach_count}/{trend.n}", threshold=cfg.trends.persistent_ratio,
               comparator=">=", temporal_scope="window_aggregate",
               note=f"mean {trend.mean_value}% used; pattern {trend.label}"),
        ]
        out.append(f)
    return out


# ----------------------------------------------------------------------------------------------
def analyze_battery(df: pd.DataFrame, cfg: RiskConfig, company_id: str) -> list[DeviceFinding]:
    bt = cfg.battery
    out: list[DeviceFinding] = []
    for device_id, g in _groups(df):
        last = g.iloc[-1]
        reported = g[g["battery_reported"].astype(bool)]
        base = dict(device_id=device_id, employee_id=last["employee_id"], hostname=last["hostname"],
                    latest_snapshot_id=last["id"], latest_collected_at=_ts(last["collected_at"]))
        if reported.empty:
            out.append(DeviceFinding(**base, pattern="no_battery_reported",
                                     current={"status": "no_battery_reported",
                                              "explanation": "No battery telemetry in any snapshot (e.g. desktop); "
                                                             "not treated as a failure."},
                                     evidence=[ev(company_id=company_id, device_id=device_id, snapshot_id=last["id"],
                                                  collected_at=_ts(last["collected_at"]), metric="battery_reported",
                                                  observed_value=False, temporal_scope="window_aggregate",
                                                  note=f"0/{len(g)} snapshots include a battery block")]))
            continue
        lr = reported.iloc[-1]
        fr = reported.iloc[0]
        latest_missing = not bool(last["battery_reported"])
        cond = lr["battery_condition"]
        service = cond in bt.service_conditions
        transitions = []
        prev = None
        for _, r in reported.iterrows():
            if prev is not None and r["battery_condition"] != prev["battery_condition"]:
                transitions.append({"from": prev["battery_condition"], "to": r["battery_condition"],
                                    "snapshot_id": r["id"], "collected_at": _ts(r["collected_at"])})
            prev = r
        fcc0, fcc1 = fr["battery_full_charge_capacity"], lr["battery_full_charge_capacity"]
        decline = round((fcc0 - fcc1) / fcc0 * 100, 2) if fcc0 and fcc1 is not None else None
        cycles = int(lr["battery_cycle_count"]) if pd.notna(lr["battery_cycle_count"]) else None
        f = DeviceFinding(
            **base,
            current={
                "condition": cond,
                "service_recommended": service,
                "cycle_count": cycles,
                "cycle_count_change": (cycles - int(fr["battery_cycle_count"])) if cycles is not None and pd.notna(fr["battery_cycle_count"]) else None,
                "full_charge_capacity": int(fcc1) if pd.notna(fcc1) else None,
                "capacity_decline_pct": decline,
                "high_cycle_count": cycles is not None and cycles >= bt.high_cycle_count,
                "latest_snapshot_missing_battery": latest_missing,
                "snapshots_missing_battery": int(len(g) - len(reported)),
                "condition_transitions": transitions,
                "measured_at_snapshot_id": lr["id"],
            },
            pattern=("service_recommended" if service else
                     "capacity_declining" if decline and decline >= bt.capacity_decline_pct else "normal"),
            flagged=service or bool(decline and decline >= bt.capacity_decline_pct),
        )
        f.evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=lr["id"],
                             collected_at=_ts(lr["collected_at"]), metric="battery_condition", observed_value=cond,
                             threshold=", ".join(bt.service_conditions), comparator="in",
                             note=("latest snapshot had no battery block; value is from the most recent snapshot "
                                   "that reported one") if latest_missing else None))
        f.evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=lr["id"],
                             collected_at=_ts(lr["collected_at"]), metric="battery_cycle_count",
                             observed_value=cycles, threshold=bt.high_cycle_count, comparator=">="))
        if decline is not None:
            f.evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=fr["id"],
                                 collected_at=_ts(fr["collected_at"]), metric="battery_full_charge_capacity",
                                 observed_value=int(fcc0), temporal_scope="historical",
                                 note="earliest reported capacity"))
            f.evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=lr["id"],
                                 collected_at=_ts(lr["collected_at"]), metric="battery_capacity_decline_pct",
                                 observed_value=decline, threshold=bt.capacity_decline_pct, comparator=">=", unit="%",
                                 temporal_scope="window_aggregate"))
        for t in transitions:
            f.evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=t["snapshot_id"],
                                 collected_at=t["collected_at"], metric="battery_condition_change",
                                 observed_value=f"{t['from']} -> {t['to']}", temporal_scope="historical"))
        out.append(f)
    return out


# ----------------------------------------------------------------------------------------------
def analyze_compliance(cdf: pd.DataFrame, cfg: RiskConfig, company_id: str, *,
                       hostnames: dict[str, str] | None = None,
                       employees: dict[str, str] | None = None) -> list[DeviceFinding]:
    out: list[DeviceFinding] = []
    for device_id, g in cdf.groupby("device_id", sort=True):
        latest_time = g["collected_at"].max()
        latest = g[g["collected_at"] == latest_time]
        checks: list[dict] = []
        evidence: list[Evidence] = []
        for check_id, cg in g.groupby("check_id", sort=True):
            cg = cg.sort_values("collected_at")
            statuses = list(cg["status"])
            lr = cg.iloc[-1]
            transitions = []
            for i in range(1, len(statuses)):
                if statuses[i] != statuses[i - 1]:
                    r = cg.iloc[i]
                    transitions.append({"from": statuses[i - 1], "to": statuses[i], "snapshot_id": r["snapshot_id"],
                                        "collected_at": _ts(r["collected_at"])})
            fails = statuses.count("fail")
            in_latest = lr["collected_at"] == latest_time
            if lr["status"] == "fail" and fails == len(statuses):
                pattern = "persistent_failure"
            elif lr["status"] == "fail" and transitions:
                pattern = "regressed"  # drift: passed earlier, failing now
            elif lr["status"] == "fail":
                pattern = "failing"
            elif fails:
                pattern = "recovered"
            else:
                pattern = "passing"
            checks.append({
                "check_id": check_id, "severity": lr["severity"], "latest_status": lr["status"] if in_latest else "not_reported",
                "pattern": pattern, "fail_count": fails, "snapshots": len(statuses), "transitions": transitions,
                "latest_snapshot_id": lr["snapshot_id"],
            })
            evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=lr["snapshot_id"],
                               collected_at=_ts(lr["collected_at"]), metric=f"compliance:{check_id}",
                               observed_value=lr["status"], threshold="pass", comparator="==", source="compliance_results",
                               note=f"severity {lr['severity']}; failed {fails}/{len(statuses)} snapshots"))
            for t in transitions:
                evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=t["snapshot_id"],
                                   collected_at=t["collected_at"], metric=f"compliance_change:{check_id}",
                                   observed_value=f"{t['from']} -> {t['to']}", source="compliance_results",
                                   temporal_scope="historical"))
        failing = [c for c in checks if c["latest_status"] == "fail"]
        latest_snap = latest.iloc[0]["snapshot_id"]
        out.append(DeviceFinding(
            device_id=device_id,
            employee_id=(employees or {}).get(device_id),
            hostname=(hostnames or {}).get(device_id),
            latest_snapshot_id=latest_snap, latest_collected_at=_ts(latest_time),
            current={"checks": checks, "failing_checks": [c["check_id"] for c in failing],
                     "failing_high_severity": [c["check_id"] for c in failing if c["severity"] == "high"]},
            pattern="failing" if failing else "compliant",
            flagged=bool(failing),
            evidence=evidence,
        ))
    return out


# ----------------------------------------------------------------------------------------------
def analyze_os(df: pd.DataFrame, cfg: RiskConfig, company_id: str) -> list[DeviceFinding]:
    out: list[DeviceFinding] = []
    for device_id, g in _groups(df):
        last = g.iloc[-1]
        product, version = last["os_product_name"], last["os_product_version"]
        major = parse_major(version)
        minimum = cfg.os.min_major_versions.get(product)
        outdated = major is not None and minimum is not None and major < minimum
        versions = list(dict.fromkeys(g["os_product_version"]))  # ordered unique
        changes = []
        for i in range(1, len(g)):
            if g.iloc[i]["os_product_version"] != g.iloc[i - 1]["os_product_version"]:
                changes.append({"from": g.iloc[i - 1]["os_product_version"], "to": g.iloc[i]["os_product_version"],
                                "snapshot_id": g.iloc[i]["id"], "collected_at": _ts(g.iloc[i]["collected_at"])})
        f = DeviceFinding(
            device_id=device_id, employee_id=last["employee_id"], hostname=last["hostname"],
            latest_snapshot_id=last["id"], latest_collected_at=_ts(last["collected_at"]),
            current={"os": product, "version": version, "build": last["os_build_version"], "major": major,
                     "policy_min_major": minimum, "below_policy_minimum": outdated,
                     "versions_seen": versions, "version_changes": changes},
            pattern="below_policy_minimum" if outdated else "current",
            flagged=outdated,
        )
        f.evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=last["id"],
                             collected_at=_ts(last["collected_at"]), metric="os_version",
                             observed_value=f"{product} {version}", threshold=f"{product} >= {minimum}" if minimum else None,
                             comparator="<" if outdated else "info"))
        out.append(f)
    return out


# ----------------------------------------------------------------------------------------------
def analyze_software(sdf: pd.DataFrame, cfg: RiskConfig, company_id: str,
                     employees: dict[str, str] | None = None) -> list[DeviceFinding]:
    """Watch-list software and first-appearance dates."""
    watch = {w.lower() for w in cfg.software_watchlist}
    out: list[DeviceFinding] = []
    for device_id, g in sdf.groupby("device_id", sort=True):
        latest_time = g["collected_at"].max()
        latest = g[g["collected_at"] == latest_time]
        hits = []
        evidence = []
        for name, ng in g[g["name"].str.lower().isin(watch)].groupby("name"):
            ng = ng.sort_values("collected_at")
            first = ng.iloc[0]
            present_now = name in set(latest["name"])
            hits.append({"name": name, "version": first["version"], "publisher": first["publisher"],
                         "first_seen_at": _ts(first["collected_at"]), "first_seen_snapshot_id": first["snapshot_id"],
                         "present_in_latest": present_now, "snapshots_present": int(len(ng))})
            evidence.append(ev(company_id=company_id, device_id=device_id, snapshot_id=first["snapshot_id"],
                               collected_at=_ts(first["collected_at"]), metric="software_first_seen",
                               observed_value=name, source="installed_software", temporal_scope="historical",
                               note="first snapshot listing this watch-listed package"))
        if hits:
            out.append(DeviceFinding(device_id=device_id, employee_id=(employees or {}).get(device_id),
                                     latest_snapshot_id=latest.iloc[0]["snapshot_id"],
                                     latest_collected_at=_ts(latest_time),
                                     current={"watchlisted_software": hits}, pattern="watchlisted_software",
                                     flagged=True, evidence=evidence))
    return out
