"""Repeatable NDJSON ingestion.

Guarantees
* Every line is parsed and validated (`RawSnapshot`); malformed lines are reported with line
  number + reason and skipped — never partially inserted.
* Snapshot identity = sha1(device_id|collected_at). Re-running ingestion inserts nothing new.
* A record whose identity already exists with *different* content is a conflict: reported, the
  stored historical snapshot is kept (history is never overwritten).
* Ownership is validated: a device must belong to exactly one company, an employee to exactly
  one company. Violations are reported and the offending records rejected.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion.schemas import RawSnapshot
from app.models import (
    Company,
    ComplianceResult,
    Device,
    DeviceSnapshot,
    DiskVolume,
    Employee,
    IngestionRun,
    InstalledSoftware,
    NetworkInterface,
)

logger = get_logger(__name__)
MAX_REPORTED_ERRORS = 100
PRIMARY_MOUNTS = ("/", "C:\\", "C:")


def snapshot_id(device_id: str, collected_at: datetime) -> str:
    key = f"{device_id}|{collected_at.astimezone(UTC).isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()


def company_display_name(company_id: str) -> str:
    """Datasets only carry slugs like `acme-001`; derive a display name ("Acme")."""
    return company_id.split("-")[0].replace("_", " ").title()


@dataclass
class IngestionStats:
    total_lines: int = 0
    blank_lines: int = 0
    parsed: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    duplicates_in_file: int = 0
    conflicts: int = 0
    malformed: int = 0
    ownership_violations: int = 0
    missing_optional: Counter = field(default_factory=Counter)
    extra_fields: Counter = field(default_factory=Counter)
    errors: list[dict] = field(default_factory=list)
    companies: int = 0
    devices: int = 0
    employees: int = 0
    min_collected_at: str | None = None
    max_collected_at: str | None = None

    def error(self, line: int, kind: str, reason: str) -> None:
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append({"line": line, "kind": kind, "reason": reason[:500]})

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["missing_optional"] = dict(self.missing_optional)
        d["extra_fields"] = dict(self.extra_fields)
        return d


def _primary_volume(rec: RawSnapshot):
    for v in rec.disk_volumes:
        if v.mount_point in PRIMARY_MOUNTS:
            return v
    return rec.disk_volumes[0]


def _build_snapshot(rec: RawSnapshot, sid: str, raw_hash: str, run_id: int) -> DeviceSnapshot:
    pv = _primary_volume(rec)
    mem = rec.memory
    bat = rec.battery
    snap = DeviceSnapshot(
        id=sid,
        device_id=rec.device_id,
        company_id=rec.company_id,
        employee_id=rec.employee_id,
        collected_at=rec.collected_at,
        agent_version=rec.agent_version,
        os_platform=rec.os.platform,
        os_product_name=rec.os.product_name,
        os_product_version=rec.os.product_version,
        os_build_version=rec.os.build_version,
        os_architecture=rec.os.architecture,
        kernel_name=rec.os.kernel_name,
        kernel_release=rec.os.kernel_release,
        hostname=rec.os.hostname,
        ram_bytes=mem.ram_bytes,
        memory_total_bytes=mem.total_memory_bytes,
        memory_used_bytes=mem.used_memory_bytes,
        memory_free_bytes=mem.free_memory_bytes,
        memory_used_pct=round(mem.used_memory_bytes / mem.total_memory_bytes * 100, 2),
        primary_disk_size_bytes=pv.size_bytes,
        primary_disk_available_bytes=pv.available_bytes,
        primary_disk_available_pct=round(pv.available_bytes / pv.size_bytes * 100, 2),
        battery_reported=bat is not None,
        battery_present=bat.battery_present if bat else None,
        battery_charging_status=bat.charging_status if bat else None,
        battery_percentage=bat.percentage if bat else None,
        battery_condition=bat.condition if bat else None,
        battery_cycle_count=bat.cycle_count if bat else None,
        battery_full_charge_capacity=bat.full_charge_capacity if bat else None,
        network_reported=bool(rec.network),
        raw_sha256=raw_hash,
        ingestion_run_id=run_id,
    )
    cid = rec.company_id
    snap.disk_volumes = [
        DiskVolume(company_id=cid, **v.model_dump(include={"volume_name", "file_system", "mount_point",
                                                            "size_bytes", "available_bytes", "encrypted"}))
        for v in rec.disk_volumes
    ]
    snap.network_interfaces = [
        NetworkInterface(company_id=cid, **n.model_dump(include={"interface_name", "address", "family",
                                                                 "internal", "mac"}))
        for n in (rec.network or [])
    ]
    snap.installed_software = [
        InstalledSoftware(company_id=cid, name=s.name, version=s.version, publisher=s.publisher)
        for s in rec.installed_software
    ]
    snap.compliance_results = [
        ComplianceResult(company_id=cid, device_id=rec.device_id, collected_at=rec.collected_at,
                         check_id=c.check_id, status=c.status, severity=(c.severity or "").lower() or None)
        for c in rec.compliance_results
    ]
    return snap


def ingest_file(session: Session, path: str | Path) -> IngestionStats:
    path = Path(path)
    data = path.read_bytes()
    run = IngestionRun(source=str(path.name), source_sha256=hashlib.sha256(data).hexdigest())
    session.add(run)
    session.flush()
    stats = IngestionStats()

    # ---- pass 1: parse + validate -------------------------------------------------------
    parsed: list[tuple[int, RawSnapshot, str]] = []
    for lineno, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        stats.total_lines += 1
        if not line.strip():
            stats.blank_lines += 1
            continue
        try:
            obj = json.loads(line)
            rec = RawSnapshot.model_validate(obj)
        except json.JSONDecodeError as exc:
            stats.malformed += 1
            stats.error(lineno, "invalid_json", str(exc))
            continue
        except ValidationError as exc:
            stats.malformed += 1
            reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
            stats.error(lineno, "schema", reason)
            continue
        if rec.battery is None:
            stats.missing_optional["battery"] += 1
        if not rec.network:
            stats.missing_optional["network"] += 1
        for k in rec.model_extra or {}:
            stats.extra_fields[k] += 1
        raw_hash = hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()
        parsed.append((lineno, rec, raw_hash))
    stats.parsed = len(parsed)

    # ---- pass 2: ownership validation (dataset + already-stored devices) ------------------
    existing_dev = {d.id: d.company_id for d in session.scalars(select(Device))}
    existing_emp = {e.id: e.company_id for e in session.scalars(select(Employee))}
    dev_companies: dict[str, set[str]] = defaultdict(set)
    emp_companies: dict[str, set[str]] = defaultdict(set)
    for _, rec, _ in parsed:
        dev_companies[rec.device_id].add(rec.company_id)
        if rec.employee_id:
            emp_companies[rec.employee_id].add(rec.company_id)
    for d, cs in dev_companies.items():
        if d in existing_dev:
            cs.add(existing_dev[d])
    for e, cs in emp_companies.items():
        if e in existing_emp:
            cs.add(existing_emp[e])
    bad_devices = {d for d, cs in dev_companies.items() if len(cs) > 1}
    bad_emps = {e for e, cs in emp_companies.items() if len(cs) > 1}

    valid: list[tuple[int, RawSnapshot, str]] = []
    for lineno, rec, h in parsed:
        if rec.device_id in bad_devices or (rec.employee_id in bad_emps):
            stats.ownership_violations += 1
            stats.error(lineno, "ownership", f"device {rec.device_id} / employee {rec.employee_id} "
                                             "maps to more than one company")
            continue
        valid.append((lineno, rec, h))

    # ---- pass 3: dedupe + upsert reference data + insert snapshots ---------------------------
    existing_snaps = {sid: h for sid, h in session.execute(select(DeviceSnapshot.id, DeviceSnapshot.raw_sha256))}
    seen_in_file: dict[str, str] = {}
    companies = {c.id for c in session.scalars(select(Company))}
    devices: dict[str, Device] = {d.id: d for d in session.scalars(select(Device))}
    employees = set(existing_emp)

    for lineno, rec, h in sorted(valid, key=lambda t: (t[1].device_id, t[1].collected_at)):
        sid = snapshot_id(rec.device_id, rec.collected_at)
        if sid in seen_in_file:
            if seen_in_file[sid] == h:
                stats.duplicates_in_file += 1
            else:
                stats.conflicts += 1
                stats.error(lineno, "conflict", f"second, different record for {rec.device_id} @ "
                                                f"{rec.collected_at.isoformat()} ignored")
            continue
        seen_in_file[sid] = h
        if sid in existing_snaps:
            if existing_snaps[sid] == h:
                stats.skipped_existing += 1
            else:
                stats.conflicts += 1
                stats.error(lineno, "conflict", f"stored snapshot {sid} differs; history not overwritten")
            continue

        if rec.company_id not in companies:
            session.add(Company(id=rec.company_id, name=company_display_name(rec.company_id)))
            companies.add(rec.company_id)
            session.flush()
        if rec.employee_id and rec.employee_id not in employees:
            session.add(Employee(id=rec.employee_id, company_id=rec.company_id))
            employees.add(rec.employee_id)
            session.flush()
        dev = devices.get(rec.device_id)
        if dev is None:
            dev = Device(id=rec.device_id, company_id=rec.company_id, first_seen_at=rec.collected_at)
            session.add(dev)
            devices[rec.device_id] = dev
        # Device row reflects the most recent snapshot; history lives in device_snapshots.
        if dev.last_seen_at is None or rec.collected_at >= dev.last_seen_at:
            ident = rec.device_identity
            dev.employee_id = rec.employee_id
            dev.serial_number = ident.serial_number
            dev.model_name = ident.model_name
            dev.model_identifier = ident.model_identifier
            dev.processor = ident.processor
            dev.hardware_uuid = ident.hardware_uuid
            dev.total_memory_label = ident.total_memory
            dev.platform = rec.os.platform
            dev.hostname = rec.os.hostname
            dev.last_seen_at = rec.collected_at
            dev.latest_snapshot_id = sid
        if dev.first_seen_at is None or rec.collected_at < dev.first_seen_at:
            dev.first_seen_at = rec.collected_at
        session.flush()
        session.add(_build_snapshot(rec, sid, h, run.id))
        stats.inserted += 1

    session.flush()
    stats.companies = len(companies)
    stats.devices = len(devices)
    stats.employees = len(employees)
    times = [rec.collected_at for _, rec, _ in valid]
    if times:
        stats.min_collected_at = min(times).isoformat()
        stats.max_collected_at = max(times).isoformat()
    run.stats = stats.as_dict()
    run.status = "completed" if stats.malformed == 0 and stats.conflicts == 0 else "completed_with_errors"
    run.finished_at = datetime.now(UTC)
    logger.info("ingestion finished", extra={"ctx": {k: v for k, v in stats.as_dict().items() if k != "errors"}})
    return stats
