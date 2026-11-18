"""PostgreSQL row-level security (defence in depth for tenant isolation)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18

Enables + forces RLS with a `tenant_isolation` policy on every tenant-owned table and creates the unprivileged
`fleetpilot_app` role that tenant-bound request transactions switch to. No-op on SQLite. See app/db/rls.py.
"""
from alembic import op

from app.db.rls import APP_ROLE, TENANT_TABLES, apply_rls, drop_sql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply_rls(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for t in TENANT_TABLES:
        for stmt in drop_sql(t):
            op.execute(stmt)
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
