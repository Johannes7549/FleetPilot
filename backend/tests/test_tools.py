from __future__ import annotations

from app.security.context import TenantContext
from app.tools import ToolContext, registry
from tests.conftest import ACME_ADMIN, ACME_VIEWER, GLOBEX_ADMIN


def run(session, tool, args, ctx=ACME_ADMIN):
    return registry.execute(tool, args, ToolContext(session=session, tenant=ctx))


def test_catalog_has_required_tools():
    names = set(registry.names())
    assert {"get_fleet_overview", "get_device_details", "find_low_storage_devices", "analyze_memory_usage",
            "analyze_battery_health", "get_compliance_status", "analyze_compliance_trends", "get_os_distribution",
            "get_installed_software", "generate_fleet_insights", "get_device_history"} <= names
    assert set(registry.names("action")) == {"create_upgrade_order", "open_remediation_ticket",
                                             "flag_device_for_replacement", "notify_employee"}


def test_cross_tenant_device_is_not_found(session):
    r = run(session, "get_device_details", {"device_id": "JRZSGXVMKE6M"})  # globex device
    assert not r.ok and r.error_code == "not_found"
    assert run(session, "get_device_details", {"device_id": "JRZSGXVMKE6M"}, GLOBEX_ADMIN).ok


def test_company_id_argument_rejected(session):
    r = run(session, "find_low_storage_devices", {"company_id": "globex-002"})
    assert not r.ok and r.error_code == "forbidden_argument"


def test_invalid_arguments(session):
    assert run(session, "find_low_storage_devices", {"threshold_pct": 500}).error_code == "invalid_arguments"
    assert run(session, "get_device_details", {"device_id": "x; DROP TABLE"}).error_code == "invalid_arguments"
    assert run(session, "get_device_details", {"unexpected": 1, "device_id": "8NM23J95R5I6"}).error_code == "invalid_arguments"
    assert run(session, "nonexistent_tool", {}).error_code == "unknown_tool"


def test_viewer_cannot_use_action_tools(session):
    r = run(session, "create_upgrade_order", {"device_id": "8NM23J95R5I6", "component": "storage", "spec": "1TB"},
            ACME_VIEWER)
    assert r.error_code == "permission_denied"


def test_every_evidence_item_is_tenant_scoped(session):
    for cid in ("acme-001", "globex-002", "initech-003"):
        ctx = TenantContext(cid, "u", "viewer")
        r = run(session, "generate_fleet_insights", {}, ctx)
        assert r.ok and r.evidence
        assert {e.company_id for e in r.evidence} == {cid}


def test_evidence_values_match_database(session):
    from app.models import DeviceSnapshot

    r = run(session, "find_low_storage_devices", {})
    for e in r.evidence:
        if e.metric == "primary_disk_available_pct":
            snap = session.get(DeviceSnapshot, e.snapshot_id)
            assert snap.company_id == "acme-001"
            assert round(snap.primary_disk_available_pct, 2) == e.observed_value


def test_empty_result_is_ok(session):
    r = run(session, "get_compliance_status", {"severity": "high", "status": "fail"})
    assert r.ok and r.data["results"] == []


def test_unknown_check_reported(session):
    r = run(session, "get_compliance_status", {"check_id": "firewall_enabled"})
    assert r.ok and "does not exist" in r.summary


def test_missing_battery_is_unknown_not_failure(session):
    r = run(session, "analyze_battery_health", {"only_flagged": False})
    mt7 = next(d for d in r.data["devices"] if d["device_id"] == "MT7PJB7N5LRE")
    assert mt7["current"]["latest_snapshot_missing_battery"] is True
    assert mt7["current"]["service_recommended"] is False
    assert "MT7PJB7N5LRE" in r.data["latest_battery_missing"]
    minis = [d for d in r.data["devices"] if d["pattern"] == "no_battery_reported"]
    assert [d["device_id"] for d in minis] == ["DLT35MHGTH2D"]
