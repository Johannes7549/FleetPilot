"""Policy configuration: thresholds, scoring weights and watch-lists.

These are *policy decisions*, not dataset facts. Defaults are documented in docs/risk-scoring.md and
can be overridden per company (Settings page -> company_settings.risk_config).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class StorageThresholds(BaseModel):
    low_pct: float = Field(10.0, gt=0, lt=100, description="Available % below which storage is 'low'")
    critical_pct: float = Field(5.0, gt=0, lt=100)
    warn_pct: float = Field(20.0, gt=0, lt=100)
    deteriorating_slope_pp_per_day: float = Field(
        -0.2, lt=0, description="Least-squares slope of available % (pp/day) at/below which a trend is deteriorating"
    )


class MemoryThresholds(BaseModel):
    high_pct: float = Field(80.0, gt=0, le=100)
    critical_pct: float = Field(90.0, gt=0, le=100)
    rising_slope_pp_per_day: float = Field(0.5, gt=0, description="Slope of used % that counts as deteriorating")


class BatteryThresholds(BaseModel):
    service_conditions: list[str] = ["Service Recommended", "Replace Soon", "Replace Now", "Service Battery"]
    high_cycle_count: int = Field(1000, gt=0)
    capacity_decline_pct: float = Field(3.0, gt=0, description="Relative full-charge-capacity drop over window")


class OSPolicy(BaseModel):
    # Minimum *major* version considered current, per OS product name. Policy, not data.
    min_major_versions: dict[str, int] = {"macOS": 15, "Windows": 11}


class TrendRules(BaseModel):
    persistent_ratio: float = Field(0.5, gt=0, le=1, description="Share of snapshots breaching => persistent")
    min_snapshots: int = Field(5, ge=2, description="Minimum snapshots before any trend classification")
    one_time_max_breaches: int = Field(1, ge=1)


class RiskWeights(BaseModel):
    storage: float = 0.25
    memory: float = 0.20
    battery: float = 0.20
    compliance: float = 0.20
    os: float = 0.15

    @model_validator(mode="after")
    def _sum_to_one(self) -> RiskWeights:
        total = self.storage + self.memory + self.battery + self.compliance + self.os
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"risk weights must sum to 1.0 (got {total:.3f})")
        return self


class RiskBands(BaseModel):
    critical: float = 70
    at_risk: float = 45
    watch: float = 20


class RiskConfig(BaseModel):
    storage: StorageThresholds = StorageThresholds()
    memory: MemoryThresholds = MemoryThresholds()
    battery: BatteryThresholds = BatteryThresholds()
    os: OSPolicy = OSPolicy()
    trends: TrendRules = TrendRules()
    weights: RiskWeights = RiskWeights()
    bands: RiskBands = RiskBands()
    software_watchlist: list[str] = Field(
        default_factory=lambda: ["uTorrent", "TeamViewer", "CleanMyMac X"],
        description="Software flagged for review when it appears (policy).",
    )


def merge_config(overrides: dict | None) -> RiskConfig:
    base = RiskConfig().model_dump()

    def deep(a: dict, b: dict) -> dict:
        for k, v in (b or {}).items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                a[k] = deep(dict(a[k]), v)
            else:
                a[k] = v
        return a

    return RiskConfig.model_validate(deep(base, overrides or {}))
