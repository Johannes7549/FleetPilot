"""Auth, health, dashboard, devices, insights, settings, tools, evidence and reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents.llm.providers import llm_status
from app.analytics.config import RiskConfig, merge_config
from app.analytics.fleet import FleetAnalysis
from app.analytics.insights import generate_insights
from app.analytics.risk import score_fleet
from app.core.config import get_settings
from app.db.session import get_db
from app.models import CompanySettings, DeviceSnapshot, IngestionRun, User
from app.repositories.telemetry import TelemetryRepository
from app.security.auth import current_tenant, require, sign
from app.security.context import TenantContext
from app.services.actions import ActionService
from app.services.audit import append_audit
from app.services.dashboard import build_dashboard
from app.services.report import build_pdf
from app.tools import ToolContext, registry

router = APIRouter(prefix="/api")


def _fa(db: Session, ctx: TenantContext) -> FleetAnalysis:
    return FleetAnalysis(TelemetryRepository(db, ctx))


# ---- auth (demo) -----------------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str


@router.get("/auth/users")
def demo_users(db: Session = Depends(get_db)):
    if get_settings().environment == "production":
        raise HTTPException(404)
    return [{"username": u.username, "display_name": u.display_name, "role": u.role, "company_id": u.company_id}
            for u in db.scalars(select(User).order_by(User.company_id, User.role))]


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    """Demo sign-in: issues a signed token for a seeded user. Replace with a real IdP in production."""
    if get_settings().environment == "production":
        raise HTTPException(404)
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None:
        raise HTTPException(401, "unknown user")
    return {"token": sign(user.id), "user": {"id": user.id, "display_name": user.display_name, "role": user.role,
                                             "company_id": user.company_id}}


@router.get("/me")
def me(ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    company = TelemetryRepository(db, ctx).company()
    return {"user_id": ctx.user_id, "display_name": ctx.display_name, "role": ctx.role,
            "company": {"id": company.id, "name": company.name},
            "permissions": sorted(p for p in ("telemetry:read", "actions:propose", "actions:approve", "settings:write")
                                  if ctx.can(p))}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    snaps = db.scalar(select(func.count()).select_from(DeviceSnapshot))
    last = db.scalar(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(1))
    llm = llm_status()
    return {"status": "ok", "database": "ok", "snapshots": snaps,
            "llm_mode": llm["active_mode"], "llm_provider": llm["provider"] or "deterministic",
            "llm_model": llm["model"] if llm["provider"] else None,
            "last_ingestion": None if last is None else {"status": last.status, "finished_at": last.finished_at,
                                                         "inserted": last.stats.get("inserted"),
                                                         "malformed": last.stats.get("malformed")}}


@router.get("/system/llm-status")
def system_llm_status(ctx: TenantContext = Depends(current_tenant)):
    """Execution mode for the UI: provider, model, stages and any fallback warning. Never returns secrets."""
    return llm_status()


# ---- dashboard / devices / insights ----------------------------------------------------------
@router.get("/dashboard")
def dashboard(ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    data = build_dashboard(_fa(db, ctx), ActionService(db, ctx))
    db.commit()
    return data


@router.get("/devices")
def devices(search: str | None = None, classification: str | None = None,
            ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    fa = _fa(db, ctx)
    risks = {r.device_id: r for r in score_fleet(fa)}
    latest = {s.device_id: s for s in fa.repo.latest_snapshots()}
    rows = []
    for d in fa.repo.devices():
        r, s = risks[d.id], latest.get(d.id)
        row = {"device_id": d.id, "employee_id": d.employee_id, "hostname": d.hostname, "model_name": d.model_name,
               "processor": d.processor, "total_memory": d.total_memory_label, "platform": d.platform,
               "os": f"{s.os_product_name} {s.os_product_version}" if s else None,
               "last_seen_at": d.last_seen_at,
               "disk_available_pct": s.primary_disk_available_pct if s else None,
               "memory_used_pct": s.memory_used_pct if s else None,
               "battery_condition": (s.battery_condition if s and s.battery_reported else None),
               "battery_reported": bool(s and s.battery_reported),
               "failing_checks": [c.check_id for c in (s.compliance_results if s else []) if c.status == "fail"],
               "risk_score": r.overall_score, "classification": r.classification, "top_category": r.top_category}
        rows.append(row)
    if search:
        q = search.lower()
        rows = [r for r in rows if any(q in str(r[k] or "").lower()
                                       for k in ("device_id", "employee_id", "hostname", "model_name", "os"))]
    if classification:
        rows = [r for r in rows if r["classification"] == classification]
    return sorted(rows, key=lambda r: -r["risk_score"])


@router.get("/devices/{device_id}")
def device_detail(device_id: str, ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    tc = ToolContext(session=db, tenant=ctx)
    details = registry.execute("get_device_details", {"device_id": device_id}, tc)
    if not details.ok:
        raise HTTPException(404 if details.error_code in ("not_found", "invalid_arguments") else 500, details.error)
    history = registry.execute("get_device_history", {"device_id": device_id}, tc)
    return {"details": details.data, "history": history.data,
            "evidence": [e.model_dump(mode="json") for e in details.evidence]}


@router.get("/insights")
def insights(ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    fa = _fa(db, ctx)
    return {"insights": [i.model_dump(mode="json") for i in generate_insights(fa)],
            "risks": [r.model_dump(mode="json") for r in score_fleet(fa)],
            "config": fa.cfg.model_dump()}


@router.get("/evidence/snapshots/{snapshot_id}")
def evidence_snapshot(snapshot_id: str, ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    """Raw stored snapshot behind an evidence reference (tenant-scoped)."""
    s = TelemetryRepository(db, ctx).snapshot(snapshot_id)
    cols = {c.name: getattr(s, c.name) for c in DeviceSnapshot.__table__.columns}
    cols.update(disk_volumes=[{k: getattr(v, k) for k in ("volume_name", "mount_point", "size_bytes", "available_bytes",
                                                         "encrypted", "file_system")} for v in s.disk_volumes],
                network_interfaces=[{k: getattr(n, k) for k in ("interface_name", "address", "family", "mac")}
                                    for n in s.network_interfaces],
                installed_software=[{"name": x.name, "version": x.version, "publisher": x.publisher} for x in s.installed_software],
                compliance_results=[{"check_id": c.check_id, "status": c.status, "severity": c.severity}
                                    for c in s.compliance_results])
    return cols


@router.get("/tools")
def tools(ctx: TenantContext = Depends(current_tenant)):
    return registry.catalog()


# ---- settings --------------------------------------------------------------------------------
@router.get("/settings")
def get_settings_route(ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db)):
    row = db.get(CompanySettings, ctx.company_id)
    return {"risk_config": merge_config(row.risk_config if row else None).model_dump(),
            "defaults": RiskConfig().model_dump(), "updated_at": row.updated_at if row else None,
            "updated_by": row.updated_by if row else None, "can_edit": ctx.can("settings:write")}


@router.put("/settings")
def put_settings(body: dict, ctx: TenantContext = Depends(require("settings:write")), db: Session = Depends(get_db)):
    try:
        cfg = merge_config(body.get("risk_config", body))
    except ValidationError as exc:
        raise HTTPException(422, [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]) from exc
    row = db.get(CompanySettings, ctx.company_id) or CompanySettings(company_id=ctx.company_id)
    old = dict(row.risk_config or {})
    row.risk_config = cfg.model_dump()
    row.updated_by = ctx.user_id
    from app.models.base import utcnow

    row.updated_at = utcnow()
    db.add(row)
    append_audit(db, company_id=ctx.company_id, actor_id=ctx.user_id, actor_type="user", event_type="settings.updated",
                 details={"before": old, "after": row.risk_config})
    db.commit()
    return {"risk_config": row.risk_config}


# ---- reports ---------------------------------------------------------------------------------
@router.get("/reports/fleet-health.pdf")
def report(ctx: TenantContext = Depends(current_tenant), db: Session = Depends(get_db),
           download: bool = Query(True)):
    pdf = build_pdf(_fa(db, ctx))
    disp = "attachment" if download else "inline"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="fleet-health-{ctx.company_id}.pdf"'})
