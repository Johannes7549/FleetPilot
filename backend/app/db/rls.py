"""PostgreSQL Row-Level Security — defence in depth for tenant isolation.

Primary isolation is in the application (every repository query filters by the session's company_id, composite
foreign keys, tool registry rejecting tenant arguments). RLS adds a database-enforced backstop: once a request
session is bound to a tenant, PostgreSQL itself refuses to return or write rows of any other tenant, even if a
query in the application forgot its filter.

How it works
* Migration 0002 enables + FORCEs RLS on every tenant-owned table with the policy
      company_id = current_setting('fleetpilot.company_id')
  when that setting is present. Sessions that are not bound to a tenant (ingestion, migrations, the demo login
  user list, background jobs) see all rows — they are system code, not request handlers.
* Superusers and table owners bypass RLS (and the official postgres image makes POSTGRES_USER a superuser), so
  bound transactions also run `SET LOCAL ROLE fleetpilot_app`: an unprivileged role (NOLOGIN, DML grants only)
  that RLS applies to. The connecting user is granted membership by the migration.
* `bind_tenant(session, company_id)` (called by the auth dependency for every authenticated request) stores the
  tenant on the session; an `after_begin` hook issues `set_config('fleetpilot.company_id', ..., is_local => true)`
  at the start of every transaction, so the value is transaction-scoped and can never leak to another request
  through the connection pool.
* SQLite (dev/tests) has no RLS: binding is a no-op there, so the SQLite workflow is unchanged.
"""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session

SETTING = "fleetpilot.company_id"
# `users` is deliberately excluded: the demo login page lists all seeded users before a tenant is known.
TENANT_TABLES = (
    "employees", "devices", "device_snapshots", "disk_volumes", "network_interfaces", "installed_software",
    "compliance_results", "company_settings", "action_proposals", "action_approvals", "audit_logs", "agent_runs",
)
POLICY = "tenant_isolation"
APP_ROLE = "fleetpilot_app"


def policy_sql(table: str) -> list[str]:
    cond = (f"current_setting('{SETTING}', true) IS NULL OR current_setting('{SETTING}', true) = '' "
            f"OR company_id = current_setting('{SETTING}', true)")
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {POLICY} ON {table}",
        f"CREATE POLICY {POLICY} ON {table} USING ({cond}) WITH CHECK ({cond})",
    ]


def drop_sql(table: str) -> list[str]:
    return [f"DROP POLICY IF EXISTS {POLICY} ON {table}", f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"]


def role_sql() -> list[str]:
    return [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        f"DO $$ BEGIN EXECUTE 'GRANT {APP_ROLE} TO ' || quote_ident(current_user); END $$",
    ]


def apply_rls(conn) -> None:
    """Idempotent; PostgreSQL only. Used by migration 0002 and by the PostgreSQL test harness."""
    if conn.dialect.name != "postgresql":
        return
    for stmt in role_sql():
        conn.exec_driver_sql(stmt)
    for t in TENANT_TABLES:
        for stmt in policy_sql(t):
            conn.exec_driver_sql(stmt)


_ready: dict[int, bool] = {}


def rls_ready(connection) -> bool:
    """True once migration 0002 (role + policies) exists in this database. Cached per engine."""
    key = id(connection.engine)
    if key not in _ready:
        _ready[key] = bool(connection.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r) AND "
            "EXISTS (SELECT 1 FROM pg_policies WHERE policyname = :p)"), {"r": APP_ROLE, "p": POLICY}).scalar())
    return _ready[key]


def bind_tenant(session: Session, company_id: str) -> None:
    session.info["rls_company_id"] = company_id
    bind = session.get_bind()
    if bind.dialect.name == "postgresql" and session.in_transaction():
        _set(session.connection(), company_id)


def _set(connection, company_id: str) -> None:
    if not rls_ready(connection):
        return  # migration 0002 not applied (e.g. a schema built with create_all); app-level isolation still applies
    connection.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
    connection.execute(text("SELECT set_config(:k, :v, true)"), {"k": SETTING, "v": company_id})


@event.listens_for(Session, "after_begin")
def _apply_tenant_setting(session: Session, transaction, connection) -> None:  # pragma: no cover - PG only
    cid = session.info.get("rls_company_id")
    if cid and connection.dialect.name == "postgresql" and session.info.get("rls_enabled", True):
        _set(connection, cid)
