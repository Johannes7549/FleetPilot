from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.security.auth import rate_limiter, sign


@pytest.fixture()
def client(fresh_maker):
    def _db():
        s = fresh_maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    rate_limiter.hits.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def H(user):
    return {"Authorization": f"Bearer {sign(user)}"}


def test_requires_auth(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers={"Authorization": "Bearer acme-admin.forged"}).status_code == 401


def test_login_and_me(client):
    tok = client.post("/api/auth/login", json={"username": "globex-viewer"}).json()["token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["company"]["id"] == "globex-002" and me["permissions"] == ["telemetry:read"]


def test_dashboard_from_database(client):
    d = client.get("/api/dashboard", headers=H("acme-viewer")).json()
    assert d["totals"]["devices"] == 10 and d["generated_from"]["snapshots"] == 300
    assert len(d["trends"]) == 30


def test_tenant_isolation_over_http(client):
    assert client.get("/api/devices/JRZSGXVMKE6M", headers=H("acme-admin")).status_code == 404
    assert client.get("/api/devices/JRZSGXVMKE6M", headers=H("globex-admin")).status_code == 200
    ids = {d["device_id"] for d in client.get("/api/devices", headers=H("acme-admin")).json()}
    assert len(ids) == 10 and "JRZSGXVMKE6M" not in ids


def test_full_approval_flow(client):
    r = client.post("/api/copilot/ask", json={"question": "Create upgrade orders for devices with low storage"},
                    headers=H("acme-operator")).json()
    pid = r["proposals"][0]["proposal_id"]
    action = client.get(f"/api/actions/{pid}", headers=H("acme-admin")).json()
    # operator cannot approve
    assert client.post(f"/api/actions/{pid}/approve", json={"payload_hash": action["payload_hash"]},
                       headers=H("acme-operator")).status_code == 403
    # other tenant cannot see it
    assert client.post(f"/api/actions/{pid}/approve", json={"payload_hash": action["payload_hash"]},
                       headers=H("globex-admin")).status_code == 404
    ok = client.post(f"/api/actions/{pid}/approve", json={"payload_hash": action["payload_hash"]},
                     headers=H("acme-admin"))
    assert ok.status_code == 200 and ok.json()["status"] == "executed"
    again = client.post(f"/api/actions/{pid}/approve", json={"payload_hash": action["payload_hash"]},
                        headers=H("acme-admin"))
    assert again.status_code == 409
    assert client.get("/api/audit/verify", headers=H("acme-admin")).json()["chain_valid"] is True


def test_settings_admin_only_and_validated(client):
    assert client.put("/api/settings", json={"risk_config": {}}, headers=H("acme-operator")).status_code == 403
    bad = client.put("/api/settings", json={"risk_config": {"weights": {"storage": 0.9}}}, headers=H("acme-admin"))
    assert bad.status_code == 422
    ok = client.put("/api/settings", json={"risk_config": {"storage": {"low_pct": 30}}}, headers=H("acme-admin"))
    assert ok.status_code == 200
    low = client.post("/api/copilot/ask", json={"question": "Which devices are low on disk space?"},
                      headers=H("acme-admin")).json()
    assert "below 30.0% free disk" in low["answer"]


def test_report_pdf(client):
    r = client.get("/api/reports/fleet-health.pdf", headers=H("initech-viewer"))
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_rate_limit(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 2)
    for _ in range(2):
        assert client.post("/api/copilot/ask", json={"question": "help"}, headers=H("acme-viewer")).status_code == 200
    assert client.post("/api/copilot/ask", json={"question": "help"}, headers=H("acme-viewer")).status_code == 429
