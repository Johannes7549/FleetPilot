"""Telemetry entities. Historical snapshots are immutable and never overwritten.

Every tenant-owned table carries `company_id`. Composite foreign keys
(`(device_id, company_id) -> devices(id, company_id)`) make it impossible at the database level
for a snapshot to reference a device that belongs to a different company.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TZDateTime, utcnow


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    __table_args__ = (UniqueConstraint("id", "company_id"),)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[str | None] = mapped_column(String(64), index=True)
    serial_number: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_identifier: Mapped[str | None] = mapped_column(String(64))
    processor: Mapped[str | None] = mapped_column(String(128))
    hardware_uuid: Mapped[str | None] = mapped_column(String(64))
    total_memory_label: Mapped[str | None] = mapped_column(String(32))
    platform: Mapped[str | None] = mapped_column(String(32))
    hostname: Mapped[str | None] = mapped_column(String(128))
    first_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    latest_snapshot_id: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        UniqueConstraint("id", "company_id"),
        ForeignKeyConstraint(["employee_id", "company_id"], ["employees.id", "employees.company_id"]),
    )


class DeviceSnapshot(Base):
    """One point-in-time telemetry record. Primary key is a content-independent deterministic
    hash of (device_id, collected_at) so re-ingestion is idempotent."""

    __tablename__ = "device_snapshots"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    company_id: Mapped[str] = mapped_column(String(64))
    employee_id: Mapped[str | None] = mapped_column(String(64))
    collected_at: Mapped[datetime] = mapped_column(TZDateTime)
    agent_version: Mapped[str | None] = mapped_column(String(32))

    os_platform: Mapped[str | None] = mapped_column(String(32))
    os_product_name: Mapped[str | None] = mapped_column(String(64))
    os_product_version: Mapped[str | None] = mapped_column(String(32))
    os_build_version: Mapped[str | None] = mapped_column(String(32))
    os_architecture: Mapped[str | None] = mapped_column(String(32))
    kernel_name: Mapped[str | None] = mapped_column(String(64))
    kernel_release: Mapped[str | None] = mapped_column(String(64))
    hostname: Mapped[str | None] = mapped_column(String(128))

    ram_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_used_pct: Mapped[float | None] = mapped_column(Float)

    # Primary volume (mount "/" or "C:\", else first) denormalised for fast fleet queries;
    # all volumes are kept in disk_volumes.
    primary_disk_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    primary_disk_available_bytes: Mapped[int | None] = mapped_column(BigInteger)
    primary_disk_available_pct: Mapped[float | None] = mapped_column(Float)

    battery_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    battery_present: Mapped[bool | None] = mapped_column(Boolean)
    battery_charging_status: Mapped[str | None] = mapped_column(String(32))
    battery_percentage: Mapped[int | None] = mapped_column(Integer)
    battery_condition: Mapped[str | None] = mapped_column(String(64))
    battery_cycle_count: Mapped[int | None] = mapped_column(Integer)
    battery_full_charge_capacity: Mapped[int | None] = mapped_column(Integer)

    network_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    ingested_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    disk_volumes: Mapped[list[DiskVolume]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    network_interfaces: Mapped[list[NetworkInterface]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    installed_software: Mapped[list[InstalledSoftware]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    compliance_results: Mapped[list[ComplianceResult]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("device_id", "collected_at"),
        ForeignKeyConstraint(["device_id", "company_id"], ["devices.id", "devices.company_id"]),
        Index("ix_snap_company_collected", "company_id", "collected_at"),
        Index("ix_snap_device_collected", "device_id", "collected_at"),
        Index("ix_snap_employee", "employee_id"),
    )


class DiskVolume(Base):
    __tablename__ = "disk_volumes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("device_snapshots.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(64), index=True)
    volume_name: Mapped[str | None] = mapped_column(String(128))
    file_system: Mapped[str | None] = mapped_column(String(32))
    mount_point: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    available_bytes: Mapped[int | None] = mapped_column(BigInteger)
    encrypted: Mapped[bool | None] = mapped_column(Boolean)


class NetworkInterface(Base):
    __tablename__ = "network_interfaces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("device_snapshots.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(64), index=True)
    interface_name: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(64))
    family: Mapped[str | None] = mapped_column(String(16))
    internal: Mapped[bool | None] = mapped_column(Boolean)
    mac: Mapped[str | None] = mapped_column(String(32))


class InstalledSoftware(Base):
    __tablename__ = "installed_software"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("device_snapshots.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    version: Mapped[str | None] = mapped_column(String(64))
    publisher: Mapped[str | None] = mapped_column(String(256))


class ComplianceResult(Base):
    __tablename__ = "compliance_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("device_snapshots.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[str] = mapped_column(String(64))
    collected_at: Mapped[datetime] = mapped_column(TZDateTime)
    check_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        Index("ix_compliance_company_check", "company_id", "check_id"),
        Index("ix_compliance_device_check_time", "device_id", "check_id", "collected_at"),
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)


class CompanySettings(Base):
    """Per-tenant policy configuration (risk thresholds/weights, software watch-list)."""

    __tablename__ = "company_settings"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    risk_config: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(64))
