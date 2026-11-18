"""PDF fleet-health report (ReportLab). Content comes from the same deterministic analytics as the UI."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.analytics.fleet import FleetAnalysis
from app.analytics.insights import generate_insights
from app.analytics.risk import score_fleet


def _esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_pdf(fa: FleetAnalysis) -> bytes:
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title="FleetPilot fleet health report")
    company = fa.repo.company()
    df = fa.snapshots
    risks = score_fleet(fa)
    insights = generate_insights(fa)
    story = [
        Paragraph(f"FleetPilot — Fleet Health Report: {_esc(company.name)}", styles["Title"]),
        Paragraph(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC}. Telemetry window "
                  f"{df['collected_at'].min():%Y-%m-%d} to {df['collected_at'].max():%Y-%m-%d} "
                  f"({len(df)} snapshots). All figures are computed deterministically from telemetry; "
                  "thresholds are configurable policy settings.", small),
        Spacer(1, 6 * mm),
        Paragraph("Fleet overview", styles["Heading2"]),
    ]
    counts = {c: sum(1 for r in risks if r.classification == c) for c in ("critical", "at_risk", "watch", "healthy")}
    t = Table([["Devices", "Critical", "At risk", "Watch", "Healthy"],
               [len(risks), counts["critical"], counts["at_risk"], counts["watch"], counts["healthy"]]])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                           ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story += [t, Spacer(1, 5 * mm), Paragraph("Identified risks", styles["Heading2"])]
    for i in insights:
        story.append(Paragraph(f"<b>[{i.severity.upper()}] {_esc(i.title)}</b> — {_esc(i.finding)}", small))
        story.append(Paragraph(f"<i>Why:</i> {_esc(i.explanation)} <i>Next step:</i> {_esc(i.recommended_next_step)}", small))
        ev = i.evidence[:4]
        if ev:
            story.append(Paragraph("<i>Evidence:</i> " + "; ".join(
                f"{_esc(e.device_id)} {_esc(e.metric)}={_esc(e.observed_value)} @ "
                f"{e.collected_at:%Y-%m-%d} (snapshot {e.snapshot_id[:10] if e.snapshot_id else '-'})"
                for e in ev if e.collected_at) + (" …" if len(i.evidence) > 4 else ""), small))
        story.append(Spacer(1, 2 * mm))
    story += [Paragraph("Compliance summary", styles["Heading2"])]
    rows = [["Check", "Severity", "Passing", "Failing"]]
    agg: dict[str, list] = {}
    for f in fa.compliance.values():
        for c in f.current["checks"]:
            a = agg.setdefault(c["check_id"], [c["severity"], 0, 0])
            a[1 if c["latest_status"] == "pass" else 2] += 1
    rows += [[k, *v] for k, v in sorted(agg.items())]
    ct = Table(rows)
    ct.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0"))]))
    story += [ct, Spacer(1, 5 * mm), Paragraph("Device risk ranking & recommended actions", styles["Heading2"])]
    rr = [["Device", "Owner", "Score", "Class", "Recommended action (requires approval)"]]
    for r in risks:
        rec = r.recommended_action
        rr.append([r.device_id, r.employee_id or "-", r.overall_score, r.classification,
                   Paragraph(_esc(f"{rec['tool']}: {rec['why']}") if rec else "-", small)])
    rt = Table(rr, colWidths=[30 * mm, 30 * mm, 14 * mm, 18 * mm, 86 * mm])
    rt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(rt)
    doc.build(story)
    return buf.getvalue()
