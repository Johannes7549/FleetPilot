from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("FLEETPILOT_LLM_PROVIDER", "deterministic")
os.environ.setdefault("FLEETPILOT_ENVIRONMENT", "test")
logging.disable(logging.CRITICAL)

from app.evaluation.harness import build_database  # noqa: E402
from app.security.context import TenantContext  # noqa: E402

TEST_DB = os.environ.get("FLEETPILOT_TEST_DATABASE_URL")  # e.g. a dedicated PostgreSQL database

ACME_ADMIN = TenantContext("acme-001", "acme-admin", "admin")
ACME_OPERATOR = TenantContext("acme-001", "acme-operator", "operator")
ACME_VIEWER = TenantContext("acme-001", "acme-viewer", "viewer")
GLOBEX_ADMIN = TenantContext("globex-002", "globex-admin", "admin")
INITECH_ADMIN = TenantContext("initech-003", "initech-admin", "admin")


@pytest.fixture(scope="session")
def maker():
    """Shared read-mostly database."""
    return build_database(TEST_DB)


@pytest.fixture()
def session(maker):
    s = maker()
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def fresh_maker():
    """A brand-new database for tests that mutate state (actions, approvals, audit)."""
    return build_database(None)


@pytest.fixture()
def fresh(fresh_maker):
    s = fresh_maker()
    yield s
    s.close()
