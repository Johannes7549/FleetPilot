"""Benchmark facts verified independently during discovery (docs/discovery.md), asserted against the
deterministic tools across all three tenants."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Company, Device, DeviceSnapshot, Employee
from app.security.context import TenantContext
from app.tools import ToolContext, registry

TENANTS = ["acme-001", "globex-002", "initech-003"]


def run_all(session, tool, args=None):
    out = {}
    for cid in TENANTS:
        r = registry.execute(tool, args or {}, ToolContext(session=session, tenant=TenantContext(cid, "t", "viewer")))
        assert r.ok, r.error
        out[cid] = r
    return out


def test_ingested_counts(session):
    assert session.scalar(select(func.count()).select_from(DeviceSnapshot)) == 750
    assert session.scalar(select(func.count()).select_from(Device)) == 25
    assert session.scalar(select(func.count()).select_from(Company)) == 3
    assert session.scalar(select(func.count()).select_from(Employee)) == 14
    per_device = session.execute(select(DeviceSnapshot.device_id, func.count()).group_by(DeviceSnapshot.device_id)).all()
    assert {n for _, n in per_device} == {30}


def test_devices_per_company(session):
    rows = dict(session.execute(select(Device.company_id, func.count()).group_by(Device.company_id)).all())
    assert rows == {"acme-001": 10, "globex-002": 8, "initech-003": 7}


def test_ten_devices_on_macos_older_than_15(session):
    res = run_all(session, "get_os_distribution", {"os_name": "macOS", "older_than_major": 15})
    ids = {d["device_id"] for r in res.values() for d in r.data["devices_older"]}
    assert ids == {"7VP16KHV88LM", "4SEFDLPTKXAM", "SPKRRDF0L786", "XZYK11QTPWEC", "1LYSSFD074BB",
                   "MT7PJB7N5LRE", "XSU9UUDGU6AC", "QPZSXKINA7GP", "DARFCP0BQM2G", "RLD7WV7J82SH"}


def test_six_devices_below_ten_percent_disk(session):
    res = run_all(session, "find_low_storage_devices", {"threshold_pct": 10})
    ids = {d["device_id"] for r in res.values() for d in r.data["low_storage_devices"]}
    assert ids == {"8NM23J95R5I6", "MT7PJB7N5LRE", "E4V0SB5I0MYU", "JRZSGXVMKE6M", "DARFCP0BQM2G", "RLD7WV7J82SH"}


def test_four_devices_persistent_ram(session):
    res = run_all(session, "analyze_memory_usage")
    ids = {d["device_id"] for r in res.values() for d in r.data["devices"] if d["trend"]["persistence"] == "persistent"}
    assert ids == {"M4XVHUV1MEPZ", "I3JLKILKQTAC", "JRZSGXVMKE6M", "CUXW0BQSV1B7"}


def test_three_batteries_service_recommended(session):
    res = run_all(session, "analyze_battery_health")
    ids = {d["device_id"] for r in res.values() for d in r.data["devices"] if d["current"].get("service_recommended")}
    assert ids == {"7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"}


def test_zero_high_severity_failures(session):
    res = run_all(session, "get_compliance_status", {"severity": "high", "status": "fail"})
    assert sum(len(r.data["results"]) for r in res.values()) == 0


def test_nineteen_devices_failing_os_up_to_date(session):
    res = run_all(session, "get_compliance_status", {"check_id": "os_up_to_date", "status": "fail"})
    assert {k: r.data["device_count"] for k, r in res.items()} == {"acme-001": 9, "globex-002": 4, "initech-003": 6}


def test_screen_lock_drift_ten_devices(session):
    res = run_all(session, "analyze_compliance_trends", {"check_id": "screen_lock"})
    ids = {d["device_id"] for r in res.values() for d in r.data["regressed"]}
    assert ids == {"1LYSSFD074BB", "2MAN6T4DJD95", "8NM23J95R5I6", "CUXW0BQSV1B7", "DLT35MHGTH2D", "DSYPSES7SKBB",
                   "JRZSGXVMKE6M", "MT7PJB7N5LRE", "SPKRRDF0L786", "XZYK11QTPWEC"}
