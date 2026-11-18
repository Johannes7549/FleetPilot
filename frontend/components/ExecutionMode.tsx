"use client";

import { AlertTriangle, Bot, Cog, Cpu } from "lucide-react";
import { Badge } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { cn, humanize } from "@/lib/format";
import type { Execution, LLMStatus, PlanStep, Validation } from "@/types/api";

export const MODE_LABEL: Record<string, string> = {
  deterministic: "Deterministic",
  llm: "LLM-powered",
  hybrid: "Hybrid",
  deterministic_fallback: "Deterministic fallback",
};

export function modeTone(mode?: string | null) {
  if (mode === "llm") return "brand";
  if (mode === "hybrid") return "ok";
  if (mode === "deterministic_fallback") return "warning";
  return "neutral";
}

export function useLLMStatus() {
  return useApi<LLMStatus>("/api/system/llm-status");
}

/** Header badge: the configured execution mode of the server. */
export function ModeBadge() {
  const { data } = useLLMStatus();
  if (!data) return null;
  const label = MODE_LABEL[data.active_mode] ?? data.active_mode;
  const detail = data.provider ? ` · ${data.provider} · ${data.model}` : "";
  return (
    <span title={data.warning ?? `Execution mode: ${label}${detail}`} className="hidden sm:inline-flex">
      <Badge tone={modeTone(data.active_mode)} className="gap-1">
        {data.active_mode === "deterministic_fallback" ? <AlertTriangle className="h-3 w-3" /> : data.provider ? <Bot className="h-3 w-3" /> : <Cog className="h-3 w-3" />}
        {label}{detail}
      </Badge>
    </span>
  );
}

/** Per-answer statement of how it was produced (Copilot). */
export function RunModeLine({ ex }: { ex?: Execution | null }) {
  if (!ex) return null;
  const tokens = ex.llm_calls.reduce((a, c) => a + (c.input_tokens ?? 0) + (c.output_tokens ?? 0), 0);
  return (
    <>
      <Badge tone={modeTone(ex.mode)}>{MODE_LABEL[ex.mode] ?? ex.mode}{ex.provider && ex.mode !== "deterministic_fallback" ? ` · ${ex.model}` : ""}</Badge>
      {ex.llm_calls.length > 0 && <span className="text-slate-400">{ex.llm_calls.length} LLM call{ex.llm_calls.length > 1 ? "s" : ""} · {tokens} tok</span>}
    </>
  );
}

export function FallbackNotice({ ex }: { ex?: Execution | null }) {
  if (!ex?.warning) return null;
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-900">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div>
        <p className="font-medium">{ex.warning}</p>
        {ex.fallbacks.length > 0 && (
          <ul className="mt-1 list-disc pl-4">
            {ex.fallbacks.map((f, i) => <li key={i}><span className="font-mono">{f.stage}</span>: {f.reason}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <div className="mt-0.5 text-sm text-slate-800">{children}</div>
    </div>
  );
}

function Check({ ok, label }: { ok: boolean | undefined; label: string }) {
  return <Badge tone={ok === false ? "error" : "ok"}>{ok === false ? "✗" : "✓"} {label}</Badge>;
}

/** Agent Activity: structured view of one run — mode, intent, plan, LLM calls, validation. */
export function RunExecutionPanel({ ex, plan, intent, validation, status }: {
  ex?: Execution | null; plan?: PlanStep[] | null; intent?: string | null; validation?: Validation | null; status: string;
}) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 sm:grid-cols-4">
        <Field label="Execution mode">
          <Badge tone={modeTone(ex?.mode)}>{MODE_LABEL[ex?.mode ?? "deterministic"] ?? ex?.mode}</Badge>
        </Field>
        <Field label="Provider / model">{ex?.provider ? <span className="font-mono text-xs">{ex.provider} · {ex.model}</span> : <span className="text-slate-500">none (rules)</span>}</Field>
        <Field label="Intent">{humanize(intent)} <span className="text-xs text-slate-500">({ex?.intent_source ?? "deterministic"})</span></Field>
        <Field label="Status">{humanize(status)}</Field>
        <Field label="Plan source">{humanize(ex?.plan_source ?? "deterministic")}</Field>
        <Field label="Answer source">{humanize(ex?.answer_source ?? "deterministic")}</Field>
        <Field label="Evidence">{validation ? `${validation.evidence_count} validated · ${humanize(validation.status)}` : "—"}</Field>
        <Field label="Tools">{validation ? `${validation.tools_ok} ok · ${validation.tools_failed} failed` : "—"}</Field>
      </div>
      <FallbackNotice ex={ex} />
      {validation?.checks && (
        <div className="flex flex-wrap gap-1.5 text-xs">
          <Check ok={validation.checks.tenant_match} label="evidence tenant matches session" />
          <Check ok={validation.checks.snapshots_exist} label="snapshots exist" />
          <Check ok={validation.checks.devices_in_tenant} label="devices belong to tenant" />
          {validation.issues.map((i, k) => <Badge key={k} tone="warning">{i}</Badge>)}
        </div>
      )}
      {!!plan?.length && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr><th className="px-3 py-1.5">#</th><th className="pr-3">Tool</th><th className="pr-3">Arguments</th><th className="pr-3">Purpose</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {plan.map((p, i) => (
                <tr key={i}>
                  <td className="px-3 py-1.5 tabular-nums text-slate-500">{i + 1}</td>
                  <td className="pr-3 font-mono text-slate-800">{p.tool}</td>
                  <td className="max-w-[260px] truncate pr-3 font-mono text-slate-600" title={JSON.stringify(p.args)}>{JSON.stringify(p.args)}</td>
                  <td className="pr-3 text-slate-600">{p.purpose}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!!ex?.llm_calls.length && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr><th className="px-3 py-1.5">LLM stage</th><th>Result</th><th>Model</th><th className="text-right">Latency</th><th className="pr-3 text-right">Tokens in → out</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {ex.llm_calls.map((c, i) => (
                <tr key={i}>
                  <td className="px-3 py-1.5"><span className="inline-flex items-center gap-1 font-mono"><Cpu className="h-3 w-3 text-slate-400" />{c.stage}</span></td>
                  <td><Badge tone={c.ok ? "ok" : "error"}>{c.ok ? "ok" : c.error_kind ?? "error"}</Badge>{!c.ok && c.error && <span className="ml-1 text-slate-500">{c.error}</span>}</td>
                  <td className="font-mono text-slate-600">{c.model}</td>
                  <td className="text-right tabular-nums">{c.latency_ms} ms</td>
                  <td className={cn("pr-3 text-right tabular-nums", !c.ok && "text-slate-400")}>{c.input_tokens ?? 0} → {c.output_tokens ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
