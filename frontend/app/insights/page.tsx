"use client";

import Link from "next/link";
import { useState } from "react";
import { EvidenceExplorer } from "@/components/EvidenceExplorer";
import { ProposeButton } from "@/components/ProposeButton";
import { RiskBadge } from "@/components/RiskBadge";
import { Badge, Card, CardBody, CardHeader, ErrorState, PageHeader, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { cn, fmtDate, humanize } from "@/lib/format";
import type { DeviceRisk, Insight } from "@/types/api";

export default function InsightsPage() {
  const { data, error, loading, reload } = useApi<{ insights: Insight[]; risks: DeviceRisk[] }>("/api/insights");
  const [cat, setCat] = useState("all");
  if (loading) return <Spinner label="Analysing 30 days of telemetry…" />;
  if (error || !data) return <ErrorState message={error?.message ?? "Error"} onRetry={reload} />;
  const cats = ["all", ...Array.from(new Set(data.insights.map((i) => i.category)))];
  const insights = data.insights.filter((i) => cat === "all" || i.category === cat);

  return (
    <>
      <PageHeader title="Insights" description="Patterns detected across every snapshot — persistent problems, deteriorating trends, drift and policy gaps" />
      <div className="mb-4 flex flex-wrap gap-1.5">
        {cats.map((c) => (
          <button key={c} onClick={() => setCat(c)} className={cn("rounded-full border px-3 py-1 text-xs font-medium",
            cat === c ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50")}>{humanize(c)}</button>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="space-y-3">
          {insights.map((i) => (
            <Card key={i.insight_id}>
              <CardBody className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={i.severity}>{i.severity}</Badge>
                  <Badge>{humanize(i.category)}</Badge>
                  <Badge tone="brand">{humanize(i.pattern_type)}</Badge>
                  <span className="ml-auto text-xs text-slate-500">{i.first_observed_at ? `${fmtDate(i.first_observed_at)} – ${fmtDate(i.last_observed_at)}` : ""}</span>
                </div>
                <div>
                  <h3 className="font-semibold text-slate-900">{i.title}</h3>
                  <p className="mt-1 text-sm text-slate-700">{i.finding}</p>
                  <p className="mt-2 text-xs text-slate-500"><b>Why:</b> {i.explanation}</p>
                  <p className="mt-1 text-xs text-slate-700"><b>Next step:</b> {i.recommended_next_step}</p>
                </div>
                {i.device_ids.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {i.device_ids.map((d) => <Link key={d} href={`/devices/${d}`} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-700 hover:bg-brand-50">{d}</Link>)}
                  </div>
                )}
                {i.recommended_actions.length > 0 && (
                  <div className="space-y-1.5 rounded-lg bg-slate-50 p-2.5">
                    {i.recommended_actions.slice(0, 6).map((a, n) => (
                      <div key={n} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                        <span>{humanize(a.tool)} · <span className="font-mono">{String(a.args.device_id ?? a.args.employee_id)}</span>{a.args.component ? ` · ${a.args.component}` : ""}{a.args.check_id ? ` · ${a.args.check_id}` : ""}</span>
                        <ProposeButton tool={a.tool} args={a.args} />
                      </div>
                    ))}
                  </div>
                )}
                <EvidenceExplorer evidence={i.evidence} />
              </CardBody>
            </Card>
          ))}
        </div>
        <Card className="h-fit">
          <CardHeader title="Risk ranking" subtitle="overall = ½·max(category) + ½·weighted mean" />
          <ul className="divide-y divide-slate-100">
            {data.risks.map((r) => (
              <li key={r.device_id} className="px-4 py-2.5">
                <div className="flex items-center justify-between">
                  <Link href={`/devices/${r.device_id}`} className="font-mono text-sm text-brand-700 hover:underline">{r.device_id}</Link>
                  <RiskBadge classification={r.classification} score={r.overall_score} />
                </div>
                <div className="mt-1.5 flex gap-1">
                  {Object.entries(r.categories).map(([k, c]) => (
                    <div key={k} className="flex-1" title={`${k}: ${c.score}`}>
                      <div className="h-1.5 rounded-full bg-slate-100"><div className="h-1.5 rounded-full bg-slate-700" style={{ width: `${c.score}%` }} /></div>
                      <p className="mt-0.5 text-[10px] text-slate-500">{k}</p>
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
