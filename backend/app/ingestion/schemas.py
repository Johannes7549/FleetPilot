"""Pydantic models describing one raw NDJSON telemetry record.

Required: identity + timestamp + os/device_identity/memory/disk_volumes/compliance_results.
Optional (observed missing in the real dataset): `battery` (null), `network` (absent).
Unknown extra keys are tolerated (forward-compatible agent versions) but counted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class RawOS(_Lenient):
    platform: str
    product_name: str
    product_version: str
    build_version: str | None = None
    architecture: str | None = None
    kernel_name: str | None = None
    kernel_release: str | None = None
    hostname: str | None = None


class RawIdentity(_Lenient):
    serial_number: str | None = None
    model_name: str | None = None
    model_identifier: str | None = None
    processor: str | None = None
    hardware_uuid: str | None = None
    total_memory: str | None = None


class RawMemory(_Lenient):
    ram_bytes: int | None = Field(default=None, ge=0)
    total_memory_bytes: int = Field(gt=0)
    used_memory_bytes: int = Field(ge=0)
    free_memory_bytes: int | None = Field(default=None, ge=0)
    page_size_bytes: int | None = None

    @model_validator(mode="after")
    def _used_le_total(self) -> RawMemory:
        if self.used_memory_bytes > self.total_memory_bytes:
            raise ValueError("used_memory_bytes exceeds total_memory_bytes")
        return self


class RawDiskVolume(_Lenient):
    volume_name: str | None = None
    file_system: str | None = None
    mount_point: str | None = None
    size_bytes: int = Field(gt=0)
    available_bytes: int = Field(ge=0)
    encrypted: bool | None = None

    @model_validator(mode="after")
    def _avail_le_size(self) -> RawDiskVolume:
        if self.available_bytes > self.size_bytes:
            raise ValueError("available_bytes exceeds size_bytes")
        return self


class RawBattery(_Lenient):
    battery_present: bool | None = None
    charging_status: str | None = None
    percentage: int | None = Field(default=None, ge=0, le=100)
    condition: str | None = None
    cycle_count: int | None = Field(default=None, ge=0)
    full_charge_capacity: int | None = Field(default=None, ge=0)


class RawNetwork(_Lenient):
    address: str | None = None
    family: str | None = None
    interface_name: str | None = None
    internal: bool | None = None
    mac: str | None = None


class RawSoftware(_Lenient):
    name: str = Field(min_length=1, max_length=256)
    version: str | None = None
    publisher: str | None = None


class RawCompliance(_Lenient):
    check_id: str = Field(min_length=1, max_length=64)
    status: str
    severity: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        v = v.lower()
        if v not in {"pass", "fail"}:
            raise ValueError(f"unknown compliance status {v!r}")
        return v


class RawSnapshot(_Lenient):
    device_id: str = Field(min_length=1, max_length=64)
    company_id: str = Field(min_length=1, max_length=64)
    employee_id: str | None = Field(default=None, max_length=64)
    collected_at: datetime
    agent_version: str | None = None
    os: RawOS
    device_identity: RawIdentity
    memory: RawMemory
    disk_volumes: list[RawDiskVolume] = Field(min_length=1)
    battery: RawBattery | None = None
    network: list[RawNetwork] | None = None
    installed_software: list[RawSoftware] = Field(default_factory=list)
    compliance_results: list[RawCompliance] = Field(default_factory=list)

    @field_validator("collected_at")
    @classmethod
    def _tz_and_sane(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("collected_at must include a timezone")
        v = v.astimezone(UTC)
        if v > datetime.now(UTC) + timedelta(days=1):
            raise ValueError("collected_at is in the future")
        if v.year < 2000:
            raise ValueError("collected_at is implausibly old")
        return v
