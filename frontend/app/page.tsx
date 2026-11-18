"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Download } from "lucide-react";
import { CLASS_COLOR, HBar, STATUS, StatusBar, TrendLine } from "@/components/charts";
import { RiskBadge } from "@/components/RiskBadge";
import { Button, Card, CardBody, CardHeader, ErrorState, PageHeader, Spinner, Stat } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { downloadReport } from "@/lib/api";
import { fmtDate, humanize } from "@/lib/format";
import type { Dashboard } from "@/types/api";

function Breakdown({ title, data, colors, order }: { title: string; data: Record<string, number>; colors: Record<string, string>; order: string[] }) {
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>
        <StatusBar segments={order.filter((k) => k in data).map((k) => ({ label: humanize(k), value: data[k], color: colors[k] ?? "#94a3b8" }))} />
      </CardBody>
    </Card>
  );
}

export default function OverviewPage() {
  const { data, error, loading, reload } = useApi<Dashboard>("/api/dashboard");
  const [busy, setBusy] = useState(false);
  if (loading) return <Spinner label="Computing fleet metrics…" />;
  if (error || !data) return <ErrorState message={error?.message ?? "No data"} onRetry={reload} />;
  const t = data.totals;

  return (
    <>
      <PageHeader
        title="Fleet overview"
        description={<>Computed live from {data.generated_from.snapshots} telemetry snapshots · {fmtDate(data.generated_from.window_start)} – {fmtDate(data.generated_from.window_end)}</>}
        actions={<Button variant="secondary" loading={busy} onClick={async () => { setBusy(true); try { await downloadReport(); } finally { setBusy(false); } }}><Download className="h-4 w-4" /> PDF report</Button>}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Devices" value={t.devices} hint={`${t.employees} employees`} />
        <Stat label="Healthy" value={t.healthy} tone="emerald" hint="risk score < 20" />
        <Stat label="Watch" value={t.watch} tone="amber" />
        <Stat label="At risk" value={t.at_risk} tone="orange" />
        <Stat label="Critical" value={t.critical} tone="rose" hint="risk score ≥ 70" />
        <Link href="/actions"><Stat label="Pending approvals" value={t.pending_approvals} hint="open Approval Center →" /></Link>
      </div>

      <Card className="mt-4">
        <CardHeader title="Risk classification" subtitle="Deterministic risk engine; weights and thresholds are configurable in Settings" />
        <CardBody>
          <StatusBar segments={(["critical", "at_risk", "watch", "healthy"] as const).map((k) => ({ label: humanize(k), value: t[k], color: CLASS_COLOR[k] }))} />
        </CardBody>
      </Card>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Breakdown title={`Storage (latest snapshot, low < ${data.thresholds.low_storage_pct}%)`} data={data.storage_health}
          colors={{ healthy: STATUS.good, warning: STATUS.warning, low: STATUS.serious, critical: STATUS.critical }} order={["critical", "low", "warning", "healthy"]} />
        <Breakdown title={`Memory (30-day pattern, > ${data.thresholds.memory_high_pct}%)`} data={data.memory_health}
          colors={{ healthy: STATUS.good, occasional_spikes: STATUS.warning, persistent_pressure: STATUS.critical }} order={["persistent_pressure", "occasional_spikes", "healthy"]} />
        <Breakdown title="Battery (latest condition)" data={data.battery_health}
          colors={{ normal: STATUS.good, service_recommended: STATUS.critical, unknown_latest: "#94a3b8", no_battery: "#cbd5e1" }} order={["service_recommended", "unknown_latest", "normal", "no_battery"]} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Average free disk space" subtitle="Mean of primary-volume available %, all devices, per day" />
          <CardBody><TrendLine data={data.trends} dataKey="avg_disk_available_pct" name="Avg free disk" unit="%" /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Devices below low-storage threshold" subtitle="Count per day" />
          <CardBody><TrendLine data={data.trends} dataKey="devices_low_storage" name="Devices" domain={[0, "auto"]} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Compliance check failure rate" subtitle="% of check results failing, per day" />
          <CardBody><TrendLine data={data.trends} dataKey="compliance_fail_rate_pct" name="Fail rate" unit="%" domain={[0, 100]} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Batteries needing service" subtitle="Devices reporting a service condition, per day" />
          <CardBody><TrendLine data={data.trends} dataKey="battery_service_recommended" name="Devices" domain={[0, "auto"]} /></CardBody>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Operating systems" subtitle="Latest snapshot per device" />
          <CardBody><HBar data={Object.entries(data.os_distribution).map(([label, value]) => ({ label, value }))} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Compliance by check" subtitle="Latest snapshot per device" />
          <CardBody className="space-y-4">
            {data.compliance.map((c) => (
              <div key={c.check_id}>
                <p className="mb-1.5 text-xs font-medium text-slate-700">{humanize(c.check_id)} <span className="text-slate-400">· {c.severity} severity</span></p>
                <StatusBar segments={[{ label: "Fail", value: c.fail, color: STATUS.critical }, { label: "Pass", value: c.pass, color: STATUS.good }]} />
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Highest-risk devices" action={<Link href="/insights" className="flex items-center gap-1 text-xs font-medium text-brand-600">Insights <ArrowRight className="h-3 w-3" /></Link>} />
          <ul className="divide-y divide-slate-100">
            {data.top_risks.map((r) => (
              <li key={r.device_id} className="px-5 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <Link href={`/devices/${r.device_id}`} className="font-mono text-sm font-medium text-brand-700 hover:underline">{r.device_id}</Link>
                  <RiskBadge classification={r.classification} score={r.overall_score} />
                </div>
                <p className="mt-0.5 truncate text-xs text-slate-500">{r.model_name} · {r.employee_id} · {r.contributing_factors[0] ?? "—"}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
