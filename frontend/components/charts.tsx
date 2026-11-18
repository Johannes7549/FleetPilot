"use client";

/* Chart conventions (dataviz reference palette):
   - one measure per chart (no dual axes), single series in categorical slot 1 (blue)
   - status colours only for health state, always paired with a text label
   - thin marks, recessive grid, hover tooltip on every chart */

import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const SERIES_1 = "#2a78d6";
export const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" } as const;
export const CLASS_COLOR: Record<string, string> = {
  healthy: STATUS.good, watch: STATUS.warning, at_risk: STATUS.serious, critical: STATUS.critical,
};
const GRID = "#e2e8f0";
const AXIS = { fontSize: 11, fill: "#64748b" };

function Tip({ active, payload, label, unit }: { active?: boolean; payload?: { value: number; name: string }[]; label?: string; unit?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs shadow-md">
      <p className="font-medium text-slate-800">{label}</p>
      {payload.map((p) => (
        <p key={p.name} className="tabular-nums text-slate-600">{p.name}: <span className="font-semibold text-slate-900">{p.value}{unit}</span></p>
      ))}
    </div>
  );
}

export function TrendLine({ data, dataKey, name, unit = "", domain, height = 160 }:
  { data: Record<string, unknown>[]; dataKey: string; name: string; unit?: string; domain?: [number | string, number | string]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="day" tick={AXIS} tickFormatter={(d: string) => d.slice(5)} interval="preserveStartEnd" minTickGap={24} axisLine={false} tickLine={false} />
        <YAxis tick={AXIS} domain={domain ?? ["auto", "auto"]} axisLine={false} tickLine={false} width={40} tickFormatter={(v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1))} />
        <Tooltip content={<Tip unit={unit} />} cursor={{ stroke: "#94a3b8", strokeDasharray: "3 3" }} />
        <Line type="monotone" dataKey={dataKey} name={name} stroke={SERIES_1} strokeWidth={2} dot={false} isAnimationActive={false} activeDot={{ r: 4, stroke: "#fff", strokeWidth: 2 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function HBar({ data, unit = "", height }: { data: { label: string; value: number; color?: string }[]; unit?: string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(120, data.length * 30 + 20)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 28, bottom: 0, left: 8 }} barCategoryGap={6}>
        <XAxis type="number" hide allowDecimals={false} />
        <YAxis type="category" dataKey="label" tick={AXIS} width={110} axisLine={false} tickLine={false} />
        <Tooltip content={<Tip unit={unit} />} cursor={{ fill: "#f1f5f9" }} />
        <Bar dataKey="value" name="Devices" isAnimationActive={false} radius={[0, 4, 4, 0]} label={{ position: "right", fontSize: 11, fill: "#334155" }}>
          {data.map((d) => <Cell key={d.label} fill={d.color ?? SERIES_1} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Segmented status bar: each segment is labelled in text (never colour alone). */
export function StatusBar({ segments }: { segments: { label: string; value: number; color: string }[] }) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  return (
    <div>
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full bg-slate-100" role="img"
        aria-label={segments.map((s) => `${s.label} ${s.value}`).join(", ")}>
        {segments.filter((s) => s.value > 0).map((s) => (
          <div key={s.label} title={`${s.label}: ${s.value}`} style={{ width: `${(s.value / total) * 100}%`, background: s.color }} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        {segments.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
            {s.label} <span className="font-semibold tabular-nums text-slate-900">{s.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
