"""`FleetAnalysis` — computes each analyzer once per request for one tenant and caches it."""

from __future__ import annotations

from functools import cached_property

import pandas as pd

from app.analytics import analyzers as az
from app.analytics.config import RiskConfig
from app.repositories.telemetry import TelemetryRepository


class FleetAnalysis:
    def __init__(self, repo: TelemetryRepository, cfg: RiskConfig | None = None) -> None:
        self.repo = repo
        self.cid = repo.cid
        self.cfg = cfg or repo.config()

    @cached_property
    def snapshots(self) -> pd.DataFrame:
        return self.repo.snapshot_frame()

    @cached_property
    def device_meta(self) -> dict[str, dict]:
        return {
            d.id: {"employee_id": d.employee_id, "hostname": d.hostname, "model_name": d.model_name,
                   "platform": d.platform, "processor": d.processor, "total_memory_label": d.total_memory_label,
                   "latest_snapshot_id": d.latest_snapshot_id, "last_seen_at": d.last_seen_at}
            for d in self.repo.devices()
        }

    def _by_device(self, findings: list[az.DeviceFinding]) -> dict[str, az.DeviceFinding]:
        return {f.device_id: f for f in findings}

    @cached_property
    def storage(self) -> dict[str, az.DeviceFinding]:
        return self._by_device(az.analyze_storage(self.snapshots, self.cfg, self.cid)) if len(self.snapshots) else {}

    @cached_property
    def memory(self) -> dict[str, az.DeviceFinding]:
        return self._by_device(az.analyze_memory(self.snapshots, self.cfg, self.cid)) if len(self.snapshots) else {}

    @cached_property
    def battery(self) -> dict[str, az.DeviceFinding]:
        return self._by_device(az.analyze_battery(self.snapshots, self.cfg, self.cid)) if len(self.snapshots) else {}

    @cached_property
    def os(self) -> dict[str, az.DeviceFinding]:
        return self._by_device(az.analyze_os(self.snapshots, self.cfg, self.cid)) if len(self.snapshots) else {}

    @cached_property
    def compliance(self) -> dict[str, az.DeviceFinding]:
        cdf = self.repo.compliance_frame()
        if cdf.empty:
            return {}
        meta = self.device_meta
        return self._by_device(az.analyze_compliance(
            cdf, self.cfg, self.cid,
            hostnames={k: v["hostname"] for k, v in meta.items()},
            employees={k: v["employee_id"] for k, v in meta.items()},
        ))

    @cached_property
    def software(self) -> dict[str, az.DeviceFinding]:
        sdf = self.repo.software_frame()
        if sdf.empty:
            return {}
        return self._by_device(az.analyze_software(
            sdf, self.cfg, self.cid, employees={k: v["employee_id"] for k, v in self.device_meta.items()}))
