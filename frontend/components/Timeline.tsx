"use client";

import { useState } from "react";
import { Bot, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Cpu, FileCheck2, Flag, ListChecks, MessageSquare, Shield, Wrench } from "lucide-react";
import { Badge } from "@/components/ui";
import { cn, humanize } from "@/lib/format";
import type { Step } from "@/types/api";

const ICONS: Record<string, React.ElementType> = {
  request_received: MessageSquare, guardrail: Shield, intent: Cpu, plan: ListChecks, tool_selection: Wrench,
  tool_call: Wrench, evidence_validation: FileCheck2, llm_call: Bot, action_proposal: Flag,
  approval_requested: Flag, response: CheckCircle2,
};

function tone(status: string) {
  if (["ok"].includes(status)) return "ok";
  if (["error", "blocked", "refused"].includes(status)) return "error";
  if (["rejected"].includes(status)) return "warning";
  return "warning";
}

export function Timeline({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <ol className="relative space-y-1">
      {steps.map((s) => {
        const Icon = ICONS[s.step_type] ?? CircleAlert;
        const tokens = (s.output as { token_usage?: { input_tokens?: number; output_tokens?: number } } | null)?.token_usage;
        return (
          <li key={s.seq} className="rounded-lg border border-slate-200 bg-white">
            <button className="flex w-full items-center gap-3 px-3 py-2 text-left" onClick={() => setOpen(open === s.seq ? null : s.seq)} aria-expanded={open === s.seq}>
              <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
                s.status === "ok" ? "bg-emerald-50 text-emerald-600" : s.status === "error" || s.status === "blocked" || s.status === "refused" ? "bg-rose-50 text-rose-600" : "bg-amber-50 text-amber-600")}>
                <Icon className="h-3.5 w-3.5" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium uppercase tracking-wide text-slate-400">{humanize(s.step_type)}</span>
                <span className="block truncate text-sm font-medium text-slate-800">{s.name}</span>
              </span>
              {tokens && <span className="hidden text-[11px] text-slate-500 sm:inline">{tokens.input_tokens ?? 0}→{tokens.output_tokens ?? 0} tok</span>}
              {!!s.evidence_ids?.length && <Badge tone="brand">{s.evidence_ids.length} ev</Badge>}
              <span className="w-14 text-right text-[11px] tabular-nums text-slate-500">{s.duration_ms ?? 0} ms</span>
              <Badge tone={tone(s.status)}>{s.status}</Badge>
              {open === s.seq ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
            </button>
            {open === s.seq && (
              <div className="grid gap-2 border-t border-slate-100 p-3 lg:grid-cols-2">
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase text-slate-500">Input</p>
                  <pre className="max-h-64 overflow-auto rounded-md bg-slate-950 p-2 text-[11px] text-slate-100">{JSON.stringify(s.input, null, 2) ?? "—"}</pre>
                </div>
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase text-slate-500">Output</p>
                  <pre className="max-h-64 overflow-auto rounded-md bg-slate-950 p-2 text-[11px] text-slate-100">{JSON.stringify(s.output, null, 2) ?? "—"}</pre>
                </div>
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
