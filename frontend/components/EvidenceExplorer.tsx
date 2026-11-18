"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Database, FileSearch } from "lucide-react";
import { api } from "@/lib/api";
import { cn, fmtDate, fmtValue, humanize } from "@/lib/format";
import { Badge, Button } from "@/components/ui";
import type { Evidence } from "@/types/api";

const SCOPE_TONE: Record<string, string> = { latest: "brand", historical: "neutral", window_aggregate: "low" };

function SnapshotViewer({ snapshotId }: { snapshotId: string }) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data) {
    return (
      <div className="mt-2">
        <Button variant="ghost" className="px-2 py-1 text-xs" loading={busy} onClick={async () => {
          setBusy(true);
          try { setData(await api(`/api/evidence/snapshots/${snapshotId}`)); } catch (e) { setErr(String((e as Error).message)); }
          setBusy(false);
        }}>
          <Database className="h-3.5 w-3.5" /> Inspect stored snapshot
        </Button>
        {err && <p className="text-xs text-rose-600">{err}</p>}
      </div>
    );
  }
  return <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-100">{JSON.stringify(data, null, 2)}</pre>;
}

export function EvidenceExplorer({ evidence, title = "Evidence", defaultOpen = false }: { evidence: Evidence[]; title?: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const rows = useMemo(() => {
    const q = filter.toLowerCase();
    return evidence.filter((e) => !q || [e.device_id, e.metric, String(e.observed_value)].some((v) => (v ?? "").toLowerCase().includes(q)));
  }, [evidence, filter]);
  if (!evidence.length) return null;

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60">
      <button className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-slate-700" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <FileSearch className="h-3.5 w-3.5 text-brand-600" /> {title}
        <Badge tone="brand" className="ml-1">{evidence.length} record{evidence.length === 1 ? "" : "s"}</Badge>
        <span className="ml-auto text-[11px] font-normal text-slate-500">from database rows — not model-generated</span>
      </button>
      {open && (
        <div className="border-t border-slate-200 p-3">
          {evidence.length > 8 && (
            <input className="mb-2 w-full rounded-md border border-slate-300 px-2 py-1 text-xs" placeholder="Filter by device, metric or value…"
              value={filter} onChange={(e) => setFilter(e.target.value)} />
          )}
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-50 text-[11px] uppercase text-slate-500">
                <tr><th className="py-1 pr-2">Device</th><th className="pr-2">Metric</th><th className="pr-2">Observed</th><th className="pr-2">Rule</th><th className="pr-2">Collected</th><th>Scope</th></tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <Fragment key={e.evidence_id}>
                    <tr className={cn("cursor-pointer border-t border-slate-200 hover:bg-white", expanded === e.evidence_id && "bg-white")}
                      onClick={() => setExpanded(expanded === e.evidence_id ? null : e.evidence_id)}>
                      <td className="py-1.5 pr-2 font-mono">{e.device_id ?? "fleet"}</td>
                      <td className="pr-2">{humanize(e.metric)}</td>
                      <td className="pr-2 font-medium tabular-nums">{fmtValue(e.observed_value)}{e.unit === "%" ? "%" : ""}</td>
                      <td className="pr-2 text-slate-500">{e.threshold !== null && e.comparator !== "info" ? `${e.comparator} ${fmtValue(e.threshold)}` : "—"}</td>
                      <td className="pr-2 text-slate-500">{fmtDate(e.collected_at, true)}</td>
                      <td><Badge tone={SCOPE_TONE[e.temporal_scope]}>{humanize(e.temporal_scope)}</Badge></td>
                    </tr>
                    {expanded === e.evidence_id && (
                      <tr>
                        <td colSpan={6} className="bg-white px-2 pb-3 text-[11px] text-slate-600">
                          <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                            <span>Evidence ID: <span className="font-mono">{e.evidence_id}</span></span>
                            <span>Company: <span className="font-mono">{e.company_id}</span></span>
                            <span>Snapshot: <span className="font-mono">{e.snapshot_id ?? "—"}</span></span>
                            <span>Source table: <span className="font-mono">{e.source}</span></span>
                            {e.note && <span className="sm:col-span-2">Note: {e.note}</span>}
                          </div>
                          {e.snapshot_id && <SnapshotViewer snapshotId={e.snapshot_id} />}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
