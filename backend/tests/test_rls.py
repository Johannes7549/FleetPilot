"""PostgreSQL row-level security (defence in depth). Runs only when FLEETPILOT_TEST_DATABASE_URL points at a
dedicated PostgreSQL database; SQLite has no RLS and the app-level isolation tests cover it."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.rls import bind_tenant
from tests.conftest import TEST_DB

pytestmark = pytest.mark.skipif(not (TEST_DB or "").startswith("postgres"), reason="PostgreSQL-only")


def companies(s, table="devices"):
    return {r[0] for r in s.execute(text(f"SELECT DISTINCT company_id FROM {table}"))}


def test_unbound_system_session_sees_all_tenants(maker):
    with maker() as s:
        assert companies(s) == {"acme-001", "globex-002", "initech-003"}


def test_bound_session_sees_only_its_tenant_even_without_filters(maker):
    with maker() as s:
        bind_tenant(s, "acme-001")
        for t in ("devices", "device_snapshots", "installed_software", "compliance_results", "employees"):
            assert companies(s, t) == {"acme-001"}, t
        assert s.execute(text("SELECT count(*) FROM devices WHERE id = 'JRZSGXVMKE6M'")).scalar() == 0  # globex
        s.commit()
        # still enforced in the next transaction (after_begin hook re-applies the transaction-local setting)
        assert companies(s) == {"acme-001"}
        assert s.execute(text("SELECT current_user")).scalar() == "fleetpilot_app"


def test_bound_session_cannot_write_other_tenant_rows(maker):
    with maker() as s:
        bind_tenant(s, "acme-001")
        with pytest.raises(DBAPIError):
            s.execute(text("INSERT INTO company_settings (company_id, risk_config, updated_at) "
                           "VALUES ('globex-002', '{}', now()) "
                           "ON CONFLICT (company_id) DO UPDATE SET risk_config = '{}'"))
        s.rollback()
        updated = s.execute(text("UPDATE devices SET hostname = hostname WHERE company_id = 'globex-002'")).rowcount
        assert updated == 0
        s.rollback()


def test_setting_does_not_leak_through_the_pool(maker):
    with maker() as s:
        bind_tenant(s, "globex-002")
        assert companies(s) == {"globex-002"}
        s.commit()
    with maker() as s2:  # likely the same pooled connection
        assert companies(s2) == {"acme-001", "globex-002", "initech-003"}
