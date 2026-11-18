"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { MODE_LABEL, RunExecutionPanel } from "@/components/ExecutionMode";
import { Timeline } from "@/components/Timeline";
import { Badge, Card, CardBody, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { cn, fmtDate, humanize } from "@/lib/format";
import type { AgentRun } from "@/types/api";

interface AuditRow { id: number; ts: string; actor_id: string; actor_type: string; event_type: string; device_id: string | null; action_proposal_id: string | null; entry_hash: string }

function RunDetail({ id }: { id: string }) {
  const { data, error, loading } = useApi<AgentRun>(`/api/agent-runs/${id}`, [id]);
  if (loading) return <Spinner />;
  if (error || !data) return <ErrorState message={error?.message ?? "Not found"} />;
  return (
    <Card>
      <CardHeader title={data.question} subtitle={`${fmtDate(data.created_at, true)} · ${data.user_id} · ${data.duration_ms} ms`}
        action={<Badge tone={data.status === "error" ? "error" : "ok"}>{humanize(data.status)}</Badge>} />
      <CardBody className="space-y-3">
        <RunExecutionPanel ex={data.execution ?? data.response?.execution} plan={data.response?.plan ?? data.plan}
          intent={data.intent} validation={data.response?.grounding?.validation ?? null} status={data.status} />
        <p className="text-xs text-slate-500">Observable execution only — intent, plan, tool calls with arguments and results, evidence validation and decisions. Total tokens: {data.token_usage?.input_tokens ?? 0} in / {data.token_usage?.output_tokens ?? 0} out. No hidden model reasoning is requested, stored or displayed.</p>
        <Timeline steps={data.steps ?? []} />
      </CardBody>
    </Card>
  );
}

function ActivityInner() {
  const params = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("run"));
  const [view, setView] = useState<"runs" | "audit">("runs");
  const runs = useApi<AgentRun[]>("/api/agent-runs?limit=100");
  const audit = useApi<AuditRow[]>(view === "audit" ? "/api/audit?limit=200" : null, [view]);
  const verify = useApi<{ chain_valid: boolean }>(view === "audit" ? "/api/audit/verify" : null, [view]);
  useEffect(() => { if (!selected && runs.data?.length) setSelected(runs.data[0].run_id); }, [runs.data, selected]);

  return (
    <>
      <PageHeader title="Agent Activity" description="Execution timelines for every copilot request, plus the tamper-evident audit log" />
      <div className="mb-4 flex gap-1.5">
        {(["runs", "audit"] as const).map((v) => (
          <button key={v} onClick={() => setView(v)} className={cn("rounded-full border px-3 py-1 text-xs font-medium",
            view === v ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-600")}>{v === "runs" ? "Agent runs" : "Audit log"}</button>
        ))}
      </div>
      {view === "runs" ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,340px)_1fr]">
          <Card className="h-fit max-h-[75vh] overflow-auto">
            {runs.loading && <Spinner />}
            {runs.data?.length === 0 && <EmptyState title="No agent runs yet" hint="Ask the Copilot a question" />}
            <ul className="divide-y divide-slate-100">
              {runs.data?.map((r) => (
                <li key={r.run_id}>
                  <button onClick={() => setSelected(r.run_id)} className={cn("w-full px-4 py-2.5 text-left hover:bg-slate-50", selected === r.run_id && "bg-brand-50")}>
                    <p className="line-clamp-2 text-sm text-slate-800">{r.question}</p>
                    <p className="mt-0.5 text-[11px] text-slate-500">{humanize(r.status)} · {MODE_LABEL[r.execution?.mode ?? ""] ?? r.provider} · {r.duration_ms} ms · {fmtDate(r.created_at, true)}</p>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          <div>{selected ? <RunDetail id={selected} /> : <Card><EmptyState title="Select a run" /></Card>}</div>
        </div>
      ) : (
        <Card className="overflow-x-auto">
          <CardHeader title="Audit log (append-only, hash-chained)"
            action={verify.data && <Badge tone={verify.data.chain_valid ? "ok" : "error"}>{verify.data.chain_valid ? "chain verified" : "CHAIN BROKEN"}</Badge>} />
          {audit.loading && <Spinner />}
          {audit.error && <ErrorState message={audit.error.message} />}
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="bg-slate-50 uppercase text-slate-500"><tr><th className="px-4 py-2">Time</th><th>Actor</th><th>Event</th><th>Device</th><th>Proposal</th><th>Entry hash</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {audit.data?.map((a) => (
                <tr key={a.id}>
                  <td className="px-4 py-2 text-slate-500">{fmtDate(a.ts, true)}</td>
                  <td>{a.actor_id} <span className="text-slate-400">({a.actor_type})</span></td>
                  <td><Badge tone={a.event_type.includes("refused") || a.event_type.includes("failed") || a.event_type.includes("blocked") ? "error" : "neutral"}>{a.event_type}</Badge></td>
                  <td className="font-mono">{a.device_id ?? "—"}</td>
                  <td className="font-mono">{a.action_proposal_id?.slice(0, 8) ?? "—"}</td>
                  <td className="font-mono text-slate-400">{a.entry_hash.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
          {audit.data?.length === 0 && <EmptyState title="No audit events yet" />}
        </Card>
      )}
    </>
  );
}

export default function ActivityPage() {
  return <Suspense fallback={<Spinner />}><ActivityInner /></Suspense>;
}
