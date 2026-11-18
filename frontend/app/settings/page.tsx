"use client";

import { useEffect, useState } from "react";
import { Button, Card, CardBody, CardHeader, ErrorState, PageHeader, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { MODE_LABEL, modeTone, useLLMStatus } from "@/components/ExecutionMode";
import { Badge } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { fmtDate } from "@/lib/format";

type Cfg = Record<string, Record<string, unknown> | unknown[]>;

const FIELDS: { group: string; key: string; label: string; help: string; step?: number }[] = [
  { group: "storage", key: "low_pct", label: "Low storage (% free)", help: "Below this is 'low' (policy)" },
  { group: "storage", key: "critical_pct", label: "Critical storage (% free)", help: "Below this is critical" },
  { group: "storage", key: "warn_pct", label: "Storage warning (% free)", help: "Early-warning band" },
  { group: "storage", key: "deteriorating_slope_pp_per_day", label: "Deteriorating slope (pp/day)", help: "Free-% slope at or below this = deteriorating", step: 0.05 },
  { group: "memory", key: "high_pct", label: "High RAM (% used)", help: "Above this counts as a high snapshot" },
  { group: "memory", key: "critical_pct", label: "Critical RAM mean (% used)", help: "Mean usage that raises the memory score" },
  { group: "trends", key: "persistent_ratio", label: "Persistent ratio", help: "Share of snapshots breaching → persistent", step: 0.05 },
  { group: "trends", key: "min_snapshots", label: "Minimum snapshots", help: "Below this: insufficient data" },
  { group: "battery", key: "high_cycle_count", label: "High cycle count", help: "Cycle count that raises battery risk" },
  { group: "battery", key: "capacity_decline_pct", label: "Capacity decline (%)", help: "Relative full-charge-capacity drop" },
  { group: "weights", key: "storage", label: "Weight: storage", help: "Weights must sum to 1.0", step: 0.05 },
  { group: "weights", key: "memory", label: "Weight: memory", help: "", step: 0.05 },
  { group: "weights", key: "battery", label: "Weight: battery", help: "", step: 0.05 },
  { group: "weights", key: "compliance", label: "Weight: compliance", help: "", step: 0.05 },
  { group: "weights", key: "os", label: "Weight: OS", help: "", step: 0.05 },
  { group: "bands", key: "critical", label: "Critical band (score ≥)", help: "" },
  { group: "bands", key: "at_risk", label: "At-risk band (score ≥)", help: "" },
  { group: "bands", key: "watch", label: "Watch band (score ≥)", help: "" },
];

export default function SettingsPage() {
  const { data, error, loading, reload } = useApi<{ risk_config: Cfg; defaults: Cfg; can_edit: boolean; updated_at: string | null; updated_by: string | null }>("/api/settings");
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setCfg(structuredClone(data.risk_config)); }, [data]);
  if (loading || !cfg) return error ? <ErrorState message={error.message} onRetry={reload} /> : <Spinner />;
  const ro = !data!.can_edit;
  const get = (g: string, k: string) => (cfg[g] as Record<string, unknown>)[k] as number;
  const set = (g: string, k: string, v: number) => setCfg({ ...cfg, [g]: { ...(cfg[g] as Record<string, unknown>), [k]: v } });
  const os = cfg.os as { min_major_versions: Record<string, number> };

  async function save(body: Cfg) {
    setBusy(true);
    setMsg(null);
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ risk_config: body }) });
      setMsg({ ok: true, text: "Saved. Insights, risk scores and copilot answers now use these thresholds." });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Settings" description={<>Risk thresholds and policy for this tenant. These are policy decisions, not dataset facts. {data!.updated_by && <>Last changed by {data!.updated_by} on {fmtDate(data!.updated_at, true)}.</>}</>}
        actions={!ro && <>
          <Button variant="secondary" onClick={() => save(data!.defaults)} disabled={busy}>Reset to defaults</Button>
          <Button onClick={() => save(cfg)} loading={busy}>Save changes</Button>
        </>} />
      {ro && <p className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Read-only: only administrators can change settings.</p>}
      {msg && <p className={`mb-4 rounded-lg p-3 text-sm ${msg.ok ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800"}`}>{msg.text}</p>}
      <ExecutionModeCard />
      <div className="grid gap-4 lg:grid-cols-2">
        {["storage", "memory", "trends", "battery", "weights", "bands"].map((g) => (
          <Card key={g}>
            <CardHeader title={g[0].toUpperCase() + g.slice(1)} />
            <CardBody className="space-y-3">
              {FIELDS.filter((f) => f.group === g).map((f) => (
                <label key={f.key} className="flex items-center justify-between gap-4 text-sm">
                  <span><span className="font-medium text-slate-800">{f.label}</span>{f.help && <span className="block text-xs text-slate-500">{f.help}</span>}</span>
                  <input type="number" step={f.step ?? 1} disabled={ro} value={get(g, f.key)} onChange={(e) => set(g, f.key, Number(e.target.value))}
                    className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-right tabular-nums disabled:bg-slate-50" />
                </label>
              ))}
            </CardBody>
          </Card>
        ))}
        <Card>
          <CardHeader title="OS policy & software watch-list" />
          <CardBody className="space-y-3 text-sm">
            {Object.entries(os.min_major_versions).map(([name, v]) => (
              <label key={name} className="flex items-center justify-between gap-4">
                <span className="font-medium text-slate-800">Minimum {name} major version</span>
                <input type="number" disabled={ro} value={v} className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-right disabled:bg-slate-50"
                  onChange={(e) => setCfg({ ...cfg, os: { min_major_versions: { ...os.min_major_versions, [name]: Number(e.target.value) } } })} />
              </label>
            ))}
            <label className="block">
              <span className="font-medium text-slate-800">Watch-listed software (comma-separated)</span>
              <input disabled={ro} value={(cfg.software_watchlist as string[]).join(", ")} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 disabled:bg-slate-50"
                onChange={(e) => setCfg({ ...cfg, software_watchlist: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </label>
          </CardBody>
        </Card>
      </div>
    </>
  );
}

function ExecutionModeCard() {
  const { data } = useLLMStatus();
  if (!data) return null;
  const rows: [string, React.ReactNode][] = [
    ["Active mode", <Badge key="m" tone={modeTone(data.active_mode)}>{MODE_LABEL[data.active_mode] ?? data.active_mode}</Badge>],
    ["Configured provider", <span key="p" className="font-mono">{data.configured_provider}</span>],
    ["Model", <span key="mo" className="font-mono">{data.model ?? "—"}</span>],
    ["API key", data.api_key_configured === null ? "not needed" : data.api_key_configured ? "configured (server-side)" : "missing"],
    ["LLM stages", data.llm_stages.length ? data.llm_stages.join(", ") : "none — rules only"],
    ["Timeout / retries", `${data.timeout_seconds}s · ${data.max_retries} SDK retries`],
  ];
  return (
    <Card className="mb-4">
      <CardHeader title="Agent execution mode" subtitle="Read-only. Configured on the server via FLEETPILOT_LLM_PROVIDER, FLEETPILOT_LLM_MODEL, FLEETPILOT_LLM_STAGES and the provider API key; secrets never reach the browser." />
      <CardBody className="space-y-3">
        {data.warning && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">⚠ {data.warning}</p>}
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {rows.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 border-b border-slate-100 pb-1.5">
              <dt className="text-slate-500">{k}</dt><dd className="text-right text-slate-800">{v}</dd>
            </div>
          ))}
        </dl>
        <p className="text-xs text-slate-500">
          Deterministic: rules plan and answer. LLM-powered: the model interprets requests, plans read tools, reviews results
          and phrases answers — all validated, with deterministic fallback. Hybrid: the model plans and reviews; answers are
          composed deterministically. In every mode, telemetry queries, risk scoring, evidence validation, tenant isolation
          and approvals stay deterministic.
        </p>
      </CardBody>
    </Card>
  );
}
