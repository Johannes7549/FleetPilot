"""Tenant-scoped read access to telemetry.

This is the *only* module that queries telemetry tables. Every query is filtered by the
authenticated `TenantContext.company_id`; IDs from another tenant behave exactly like unknown IDs.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.analytics.config import RiskConfig, merge_config
from app.core.errors import TenantAccessError
from app.models import (
    Company,
    CompanySettings,
    ComplianceResult,
    Device,
    DeviceSnapshot,
    Employee,
    InstalledSoftware,
)
from app.security.context import TenantContext

SNAPSHOT_COLUMNS = [
    "id", "device_id", "company_id", "employee_id", "collected_at", "os_platform", "os_product_name",
    "os_product_version", "os_build_version", "hostname", "memory_total_bytes", "memory_used_bytes",
    "memory_used_pct", "primary_disk_size_bytes", "primary_disk_available_bytes",
    "primary_disk_available_pct", "battery_reported", "battery_present", "battery_condition",
    "battery_cycle_count", "battery_full_charge_capacity", "battery_percentage", "network_reported",
]


class TelemetryRepository:
    def __init__(self, session: Session, ctx: TenantContext) -> None:
        self.s = session
        self.ctx = ctx
        self.cid = ctx.company_id

    # ---- reference data -------------------------------------------------------------------
    def company(self) -> Company:
        c = self.s.get(Company, self.cid)
        if c is None:
            raise TenantAccessError("company not found")
        return c

    def config(self) -> RiskConfig:
        row = self.s.get(CompanySettings, self.cid)
        return merge_config(row.risk_config if row else None)

    def devices(self) -> list[Device]:
        return list(self.s.scalars(select(Device).where(Device.company_id == self.cid).order_by(Device.id)))

    def device(self, device_id: str) -> Device:
        d = self.s.scalar(select(Device).where(and_(Device.id == device_id, Device.company_id == self.cid)))
        if d is None:
            # Same error whether the device doesn't exist or belongs to another tenant.
            raise TenantAccessError(f"device {device_id!r} not found in your organisation")
        return d

    def device_exists(self, device_id: str) -> bool:
        return self.s.scalar(
            select(Device.id).where(and_(Device.id == device_id, Device.company_id == self.cid))
        ) is not None

    def employees(self) -> list[Employee]:
        return list(self.s.scalars(select(Employee).where(Employee.company_id == self.cid).order_by(Employee.id)))

    def employee(self, employee_id: str) -> Employee:
        e = self.s.scalar(select(Employee).where(and_(Employee.id == employee_id, Employee.company_id == self.cid)))
        if e is None:
            raise TenantAccessError(f"employee {employee_id!r} not found in your organisation")
        return e

    # ---- snapshots --------------------------------------------------------------------------
    def latest_snapshots(self) -> list[DeviceSnapshot]:
        q = (
            select(DeviceSnapshot)
            .join(Device, and_(Device.latest_snapshot_id == DeviceSnapshot.id, Device.company_id == self.cid))
            .where(DeviceSnapshot.company_id == self.cid)
            .order_by(DeviceSnapshot.device_id)
        )
        return list(self.s.scalars(q))

    def snapshot(self, snapshot_id: str) -> DeviceSnapshot:
        snap = self.s.scalar(
            select(DeviceSnapshot).where(
                and_(DeviceSnapshot.id == snapshot_id, DeviceSnapshot.company_id == self.cid)
            )
        )
        if snap is None:
            raise TenantAccessError(f"snapshot {snapshot_id!r} not found in your organisation")
        return snap

    def snapshot_ids_exist(self, snapshot_ids: set[str]) -> set[str]:
        """Return the subset of IDs that exist *within this tenant*."""
        if not snapshot_ids:
            return set()
        rows = self.s.scalars(
            select(DeviceSnapshot.id).where(
                and_(DeviceSnapshot.id.in_(snapshot_ids), DeviceSnapshot.company_id == self.cid)
            )
        )
        return set(rows)

    def device_snapshots(self, device_id: str, since: datetime | None = None) -> list[DeviceSnapshot]:
        self.device(device_id)  # tenant check
        q = select(DeviceSnapshot).where(
            and_(DeviceSnapshot.device_id == device_id, DeviceSnapshot.company_id == self.cid)
        )
        if since is not None:
            q = q.where(DeviceSnapshot.collected_at >= since)
        return list(self.s.scalars(q.order_by(DeviceSnapshot.collected_at)))

    def snapshot_frame(self, device_id: str | None = None) -> pd.DataFrame:
        cols = [getattr(DeviceSnapshot, c) for c in SNAPSHOT_COLUMNS]
        q = select(*cols).where(DeviceSnapshot.company_id == self.cid)
        if device_id:
            q = q.where(DeviceSnapshot.device_id == device_id)
        rows = self.s.execute(q.order_by(DeviceSnapshot.device_id, DeviceSnapshot.collected_at)).all()
        df = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
        if not df.empty:
            df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True)
        return df

    def compliance_frame(self, device_id: str | None = None) -> pd.DataFrame:
        cols = ["snapshot_id", "device_id", "collected_at", "check_id", "status", "severity"]
        q = select(
            ComplianceResult.snapshot_id, ComplianceResult.device_id, ComplianceResult.collected_at,
            ComplianceResult.check_id, ComplianceResult.status, ComplianceResult.severity,
        ).where(ComplianceResult.company_id == self.cid)
        if device_id:
            q = q.where(ComplianceResult.device_id == device_id)
        df = pd.DataFrame(self.s.execute(q).all(), columns=cols)
        if not df.empty:
            df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True)
            df = df.sort_values(["device_id", "check_id", "collected_at"]).reset_index(drop=True)
        return df

    def software_frame(self, device_id: str | None = None) -> pd.DataFrame:
        cols = ["snapshot_id", "device_id", "collected_at", "name", "version", "publisher"]
        q = (
            select(
                InstalledSoftware.snapshot_id, DeviceSnapshot.device_id, DeviceSnapshot.collected_at,
                InstalledSoftware.name, InstalledSoftware.version, InstalledSoftware.publisher,
            )
            .join(DeviceSnapshot, DeviceSnapshot.id == InstalledSoftware.snapshot_id)
            .where(and_(InstalledSoftware.company_id == self.cid, DeviceSnapshot.company_id == self.cid))
        )
        if device_id:
            q = q.where(DeviceSnapshot.device_id == device_id)
        df = pd.DataFrame(self.s.execute(q).all(), columns=cols)
        if not df.empty:
            df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True)
        return df
