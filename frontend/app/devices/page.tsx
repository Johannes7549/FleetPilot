"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { RiskBadge } from "@/components/RiskBadge";
import { Badge, Card, EmptyState, ErrorState, PageHeader, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { cn, fmtDate, fmtPct, humanize } from "@/lib/format";
import type { DeviceRow } from "@/types/api";

const CLASSES = ["all", "critical", "at_risk", "watch", "healthy"] as const;

export default function DevicesPage() {
  const { data, error, loading, reload } = useApi<DeviceRow[]>("/api/devices");
  const [q, setQ] = useState("");
  const [cls, setCls] = useState<(typeof CLASSES)[number]>("all");
  const rows = useMemo(() => (data ?? []).filter((d) =>
    (cls === "all" || d.classification === cls) &&
    (!q || [d.device_id, d.employee_id, d.hostname, d.model_name, d.os].some((v) => (v ?? "").toLowerCase().includes(q.toLowerCase())))), [data, q, cls]);

  return (
    <>
      <PageHeader title="Devices" description="Inventory with latest telemetry and deterministic risk classification" />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search device, employee, hostname, model, OS…" aria-label="Search devices"
            className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm" />
        </div>
        <div className="flex rounded-lg border border-slate-300 bg-white p-0.5">
          {CLASSES.map((c) => (
            <button key={c} onClick={() => setCls(c)} className={cn("rounded-md px-3 py-1.5 text-xs font-medium", cls === c ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100")}>
              {humanize(c)}
            </button>
          ))}
        </div>
      </div>
      {loading && <Spinner />}
      {error && <ErrorState message={error.message} onRetry={reload} />}
      {data && (
        <Card className="overflow-x-auto">
          {rows.length === 0 ? <EmptyState title="No devices match" hint="Clear the search or filter" /> : (
            <table className="w-full min-w-[960px] text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2.5">Device</th><th className="px-3">Owner</th><th className="px-3">Hardware</th><th className="px-3">OS</th>
                  <th className="px-3 text-right">Free disk</th><th className="px-3 text-right">RAM used</th><th className="px-3">Battery</th>
                  <th className="px-3">Failing checks</th><th className="px-3">Risk</th><th className="px-3">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((d) => (
                  <tr key={d.device_id} className="hover:bg-slate-50">
                    <td className="px-4 py-2.5">
                      <Link href={`/devices/${d.device_id}`} className="font-mono font-medium text-brand-700 hover:underline">{d.device_id}</Link>
                      <p className="text-xs text-slate-500">{d.hostname}</p>
                    </td>
                    <td className="px-3 font-mono text-xs">{d.employee_id}</td>
                    <td className="px-3 text-xs">{d.model_name}<p className="text-slate-500">{d.processor} · {d.total_memory}</p></td>
                    <td className="px-3 text-xs">{d.os}</td>
                    <td className={cn("px-3 text-right tabular-nums", (d.disk_available_pct ?? 100) < 10 && "font-semibold text-rose-700")}>{fmtPct(d.disk_available_pct)}</td>
                    <td className={cn("px-3 text-right tabular-nums", (d.memory_used_pct ?? 0) > 80 && "font-semibold text-orange-700")}>{fmtPct(d.memory_used_pct)}</td>
                    <td className="px-3 text-xs">{d.battery_reported ? d.battery_condition : <span className="text-slate-400">not reported</span>}</td>
                    <td className="px-3"><div className="flex flex-wrap gap-1">{d.failing_checks.map((c) => <Badge key={c} tone="medium">{humanize(c)}</Badge>)}</div></td>
                    <td className="px-3"><RiskBadge classification={d.classification} score={d.risk_score} /></td>
                    <td className="px-3 text-xs text-slate-500">{fmtDate(d.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </>
  );
}
