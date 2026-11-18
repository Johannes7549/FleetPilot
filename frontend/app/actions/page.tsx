"use client";

import { useState } from "react";
import { Check, ShieldCheck, X } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";
import { EvidenceExplorer } from "@/components/EvidenceExplorer";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { api, ApiError } from "@/lib/api";
import { cn, fmtDate, humanize } from "@/lib/format";
import type { ActionProposal } from "@/types/api";

const TABS = ["pending_approval", "executed", "rejected", "failed", "expired", "all"] as const;

function Detail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { can, me } = useAuth();
  const { data: p, error, loading, reload } = useApi<ActionProposal>(`/api/actions/${id}`, [id]);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (loading) return <Spinner />;
  if (error || !p) return <ErrorState message={error?.message ?? "Not found"} />;

  async function decide(kind: "approve" | "reject") {
    setBusy(kind);
    setMsg(null);
    try {
      // The approver confirms the exact payload hash they reviewed; the server refuses any mismatch.
      const r = await api<ActionProposal>(`/api/actions/${p!.id}/${kind}`, { method: "POST", body: JSON.stringify({ payload_hash: p!.payload_hash, comment: comment || null }) });
      setMsg({ ok: true, text: kind === "approve" ? `Approved → ${r.status}${r.external_ref ? ` (${r.external_ref})` : ""}` : "Rejected — this action will never execute." });
      reload();
      onChanged();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
      reload();
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader title={humanize(p.action_type)} subtitle={<span className="font-mono">{p.id}</span>} action={<Badge tone={p.status}>{humanize(p.status)}</Badge>} />
      <CardBody className="space-y-4 text-sm">
        <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
          {Object.entries(p.params).map(([k, v]) => <div key={k}><span className="text-slate-500">{humanize(k)}:</span> <span className="font-medium">{v}</span></div>)}
          <div><span className="text-slate-500">Proposed by:</span> {p.proposed_by} {p.agent_run_id && <Badge className="ml-1">via agent</Badge>}</div>
          <div><span className="text-slate-500">Created:</span> {fmtDate(p.created_at, true)}</div>
          <div><span className="text-slate-500">Expires:</span> {fmtDate(p.expires_at, true)}</div>
          {p.decided_by && <div><span className="text-slate-500">Decided by:</span> {p.decided_by} at {fmtDate(p.decided_at, true)}</div>}
        </div>
        <div className="rounded-lg bg-slate-50 p-3">
          <p className="text-xs font-medium uppercase text-slate-500">Justification (computed from telemetry)</p>
          <p className="mt-1 text-slate-800">{p.justification}</p>
        </div>
        <EvidenceExplorer evidence={p.evidence} title="Supporting evidence bound to this proposal" defaultOpen />
        <p className="break-all text-[11px] text-slate-500"><ShieldCheck className="mr-1 inline h-3.5 w-3.5 text-emerald-600" />Payload hash (action + parameters + evidence): <span className="font-mono">{p.payload_hash}</span></p>
        {p.execution_result && (
          <div className="rounded-lg border border-slate-200 p-3">
            <p className="text-xs font-medium uppercase text-slate-500">Execution result</p>
            <pre className="mt-1 overflow-auto text-xs">{JSON.stringify(p.execution_result, null, 2)}</pre>
          </div>
        )}
        {p.status === "pending_approval" && (
          can("actions:approve") ? (
            <div className="space-y-2 border-t border-slate-100 pt-3">
              <textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} rows={2} placeholder="Comment (optional)"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              <div className="flex gap-2">
                <Button variant="success" loading={busy === "approve"} disabled={!!busy} onClick={() => decide("approve")}><Check className="h-4 w-4" /> Approve & execute</Button>
                <Button variant="secondary" loading={busy === "reject"} disabled={!!busy} onClick={() => decide("reject")}><X className="h-4 w-4" /> Reject</Button>
              </div>
              <p className="text-[11px] text-slate-500">Signed in as {me?.user_id}. Execution uses the simulated integration (no external system is called).</p>
            </div>
          ) : <p className="border-t border-slate-100 pt-3 text-xs text-slate-500">Only administrators can approve or reject. Your role: {me?.role}.</p>
        )}
        {msg && <p className={cn("rounded-lg p-2 text-sm", msg.ok ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800")}>{msg.text}</p>}
        {!!p.history?.length && (
          <div>
            <p className="mb-1 text-xs font-medium uppercase text-slate-500">Approval history</p>
            <ul className="space-y-1 text-xs">
              {p.history.map((h, i) => (
                <li key={i} className="flex flex-wrap gap-2">
                  <span className="text-slate-500">{fmtDate(h.created_at, true)}</span>
                  <span className="font-medium">{h.approver_id}</span> {h.decision}
                  <Badge tone={h.outcome === "accepted" ? "ok" : "error"}>{humanize(h.outcome)}</Badge>
                  {h.comment && <span className="text-slate-500">“{h.comment}”</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export default function ActionsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("pending_approval");
  const { data, error, loading, reload } = useApi<ActionProposal[]>(`/api/actions${tab === "all" ? "" : `?status=${tab}`}`, [tab]);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <>
      <PageHeader title="Approval Center" description="Every state-changing action is proposed with evidence and executes only after an authorised human approves the exact payload" />
      <div className="mb-4 flex flex-wrap gap-1.5">
        {TABS.map((t) => (
          <button key={t} onClick={() => { setTab(t); setSelected(null); }} className={cn("rounded-full border px-3 py-1 text-xs font-medium",
            tab === t ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-600")}>{humanize(t)}</button>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,380px)_1fr]">
        <Card className="h-fit">
          {loading && <Spinner />}
          {error && <ErrorState message={error.message} onRetry={reload} />}
          {data && data.length === 0 && <EmptyState title="No actions here" hint="Ask the Copilot to propose one, or use Propose on an insight" />}
          <ul className="divide-y divide-slate-100">
            {data?.map((p) => (
              <li key={p.id}>
                <button onClick={() => setSelected(p.id)} className={cn("w-full px-4 py-3 text-left hover:bg-slate-50", selected === p.id && "bg-brand-50")}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-slate-800">{humanize(p.action_type)}</span>
                    <Badge tone={p.status}>{humanize(p.status)}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500"><span className="font-mono">{p.device_id ?? p.employee_id}</span> · {fmtDate(p.created_at, true)}</p>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        <div>{selected ? <Detail id={selected} onChanged={reload} /> : <Card><EmptyState title="Select an action" hint="Review its evidence and justification before deciding" /></Card>}</div>
      </div>
    </>
  );
}
