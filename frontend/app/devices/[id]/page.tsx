"use client";

import Link from "next/link";
import { use } from "react";
import { ArrowLeft } from "lucide-react";
import { TrendLine } from "@/components/charts";
import { EvidenceExplorer } from "@/components/EvidenceExplorer";
import { ProposeButton } from "@/components/ProposeButton";
import { RiskBadge } from "@/components/RiskBadge";
import { Badge, Card, CardBody, CardHeader, ErrorState, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { fmtDate, fmtPct, humanize } from "@/lib/format";
import type { DeviceRisk, Evidence } from "@/types/api";

interface Detail {
  details: {
    device: Record<string, string>;
    latest_snapshot: {
      snapshot_id: string; collected_at: string; os: string; memory_used_pct: number; disk_available_pct: number;
      battery: { condition: string; cycle_count: number; full_charge_capacity: number; percentage: number } | null;
      battery_reported: boolean; network_reported: boolean; installed_software: string[];
      compliance: { check_id: string; status: string; severity: string }[];
      volumes: { name: string; mount: string; encrypted: boolean; size_gb: number; available_gb: number }[];
    } | null;
    risk: DeviceRisk;
  };
  history: { series: Record<string, unknown>[]; trends: Record<string, { pattern: string; trend: Record<string, number> | null }> };
  evidence: Evidence[];
}

export default function DevicePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, loading, reload } = useApi<Detail>(`/api/devices/${id}`);
  if (loading) return <Spinner />;
  if (error || !data) return <ErrorState message={error?.status === 404 ? `Device ${id} was not found in your organisation.` : error?.message ?? "Error"} onRetry={reload} />;
  const { device, latest_snapshot: s, risk } = data.details;
  const series: Record<string, unknown>[] = data.history.series.map((r) => ({ ...r, day: String(r.collected_at).slice(0, 10) }));

  return (
    <>
      <Link href="/devices" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" /> Devices</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-mono text-xl font-semibold text-slate-900">{device.device_id}</h1>
          <p className="text-sm text-slate-500">{device.model_name} · {device.processor} · {device.total_memory} · {device.hostname} · owner <span className="font-mono">{device.employee_id}</span></p>
        </div>
        <RiskBadge classification={risk.classification} score={risk.overall_score} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Latest snapshot" subtitle={s ? fmtDate(s.collected_at, true) : "—"} />
          {s && (
            <CardBody className="space-y-2 text-sm">
              <Row k="OS" v={s.os} />
              <Row k="Free disk" v={fmtPct(s.disk_available_pct)} />
              <Row k="RAM used" v={fmtPct(s.memory_used_pct)} />
              <Row k="Battery" v={s.battery_reported && s.battery ? `${s.battery.condition} · ${s.battery.cycle_count} cycles` : "not reported (unknown)"} />
              <Row k="Network" v={s.network_reported ? "reported" : "not reported"} />
              <div className="pt-2">
                <p className="mb-1 text-xs font-medium text-slate-500">Compliance</p>
                <div className="flex flex-wrap gap-1">{s.compliance.map((c) => <Badge key={c.check_id} tone={c.status === "pass" ? "healthy" : "critical"}>{humanize(c.check_id)}: {c.status}</Badge>)}</div>
              </div>
              <div className="pt-2">
                <p className="mb-1 text-xs font-medium text-slate-500">Installed software</p>
                <p className="text-xs text-slate-600">{s.installed_software.join(", ")}</p>
              </div>
            </CardBody>
          )}
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Risk breakdown" subtitle="Deterministic scoring — see Settings for weights" />
          <CardBody>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              {Object.entries(risk.categories).map(([k, c]) => (
                <div key={k} className="rounded-lg border border-slate-200 p-2.5">
                  <p className="text-xs uppercase text-slate-500">{k}</p>
                  <p className="text-lg font-semibold tabular-nums">{c.score}</p>
                  <p className="text-[11px] text-slate-500">{humanize(c.issue_type)}</p>
                </div>
              ))}
            </div>
            <ul className="mt-3 space-y-1 text-sm text-slate-700">{risk.contributing_factors.map((f) => <li key={f}>• {f}</li>)}</ul>
            {risk.recommended_action && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-brand-50 p-3 text-sm">
                <span>Recommended: <b>{humanize(risk.recommended_action.tool)}</b> — {risk.recommended_action.why}</span>
                <ProposeButton tool={risk.recommended_action.tool} args={risk.recommended_action.args} />
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Free disk %" subtitle={`Pattern: ${humanize(data.history.trends.storage?.pattern)}`} />
          <CardBody><TrendLine data={series} dataKey="disk_available_pct" name="Free disk" unit="%" domain={[0, "auto"]} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="RAM used %" subtitle={`Pattern: ${humanize(data.history.trends.memory?.pattern)}`} />
          <CardBody><TrendLine data={series} dataKey="memory_used_pct" name="RAM used" unit="%" domain={[0, 100]} /></CardBody>
        </Card>
        {series.some((r) => r.battery_reported) && (
          <Card>
            <CardHeader title="Battery cycle count" subtitle="Gaps are snapshots without battery telemetry" />
            <CardBody><TrendLine data={series} dataKey="battery_cycle_count" name="Cycles" /></CardBody>
          </Card>
        )}
        <Card>
          <CardHeader title="Compliance history" subtitle="One cell per snapshot (oldest → newest)" />
          <CardBody className="space-y-2">
            {Object.keys((series[0]?.compliance as Record<string, string>) ?? {}).map((check) => (
              <div key={check}>
                <p className="mb-1 text-xs text-slate-600">{humanize(check)}</p>
                <div className="flex gap-[2px]">
                  {series.map((r, i) => {
                    const st = (r.compliance as Record<string, string>)[check];
                    return <div key={i} title={`${r.day}: ${st}`} className="h-4 flex-1 rounded-sm" style={{ background: st === "pass" ? "#0ca30c" : "#d03b3b" }} />;
                  })}
                </div>
              </div>
            ))}
            <p className="text-[11px] text-slate-500">Green = pass, red = fail (hover a cell for date and status).</p>
          </CardBody>
        </Card>
      </div>
      <div className="mt-4"><EvidenceExplorer evidence={data.evidence} title="Evidence behind this device's risk score" /></div>
    </>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3"><span className="text-slate-500">{k}</span><span className="text-right font-medium text-slate-800">{v}</span></div>;
}
