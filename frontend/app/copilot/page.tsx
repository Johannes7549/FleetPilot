"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Activity, Bot, CheckCircle2, CircleX, Send, ShieldAlert, Sparkles, User, Wrench } from "lucide-react";
import { EvidenceExplorer } from "@/components/EvidenceExplorer";
import { FallbackNotice, RunModeLine } from "@/components/ExecutionMode";
import { Badge, Button, Card } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { humanize } from "@/lib/format";
import type { AgentResponse, AgentRun } from "@/types/api";

const SUGGESTED = [
  "What are the biggest fleet health risks this month?",
  "Which devices are running low on disk space?",
  "Which devices have consistently high RAM utilization?",
  "Show me devices failing high-severity compliance checks.",
  "How many devices are running an OS older than macOS 15?",
  "Which employees have devices with battery service recommendations?",
  "Is screen lock compliance getting worse over time?",
  "Create upgrade orders for devices with low storage",
];

const STATUS_TONE: Record<string, string> = {
  answered: "ok", partial: "warning", awaiting_approval: "brand", action_refused: "warning", refused: "error",
  cannot_answer: "neutral", not_found: "warning", insufficient_evidence: "warning", error: "error",
  needs_clarification: "warning",
};

interface Turn { question: string; response?: AgentResponse; error?: string; pending?: boolean }

function newId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
}

function AnswerCard({ r }: { r: AgentResponse }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>{humanize(r.status)}</Badge>
        {r.intent && <Badge>intent: {humanize(r.intent)}</Badge>}
        <Badge tone={r.grounding.method === "llm_verified" || r.grounding.method === "deterministic" ? "ok" : "warning"}>
          {r.grounding.method === "deterministic" ? "deterministic answer" : humanize(r.grounding.method)}
        </Badge>
        <RunModeLine ex={r.execution} />
        <span className="text-slate-400">{r.duration_ms} ms</span>
        <Link href={`/activity?run=${r.run_id}`} className="ml-auto inline-flex items-center gap-1 text-brand-600 hover:underline">
          <Activity className="h-3 w-3" /> execution timeline
        </Link>
      </div>
      <FallbackNotice ex={r.execution} />
      {r.guard?.blocked && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" /> Guardrail: {humanize(r.guard.code)}
        </div>
      )}
      <div className="prose-answer text-sm leading-relaxed text-slate-700"><ReactMarkdown>{r.answer}</ReactMarkdown></div>
      {r.tools_used.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {r.tools_used.map((t, i) => (
            <span key={i} title={t.error ?? JSON.stringify(t.args)}
              className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] text-slate-600">
              {t.ok ? <CheckCircle2 className="h-3 w-3 text-emerald-600" /> : <CircleX className="h-3 w-3 text-rose-600" />}
              <Wrench className="h-3 w-3" /> {t.tool} <span className="text-slate-400">{t.duration_ms}ms</span>
            </span>
          ))}
        </div>
      )}
      {r.proposals.length > 0 && (
        <div className="rounded-lg border border-brand-100 bg-brand-50/60 p-3 text-sm">
          <p className="font-medium text-brand-900">{r.proposals.length} action proposal(s) awaiting human approval</p>
          <ul className="mt-1 space-y-0.5 text-xs text-brand-900/80">
            {r.proposals.map((p) => (
              <li key={p.proposal_id}>• {humanize(p.action_type)} — {p.params.device_id ?? p.params.employee_id} <span className="font-mono">({p.proposal_id.slice(0, 8)})</span></li>
            ))}
          </ul>
          <Link href="/actions" className="mt-2 inline-block text-xs font-medium text-brand-700 hover:underline">Review in Approval Center →</Link>
        </div>
      )}
      <EvidenceExplorer evidence={r.evidence} />
    </div>
  );
}

export default function CopilotPage() {
  const [conversationId, setConversationId] = useState<string>(newId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recent, setRecent] = useState<AgentRun[]>([]);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);
  useEffect(() => { api<AgentRun[]>("/api/agent-runs?limit=12").then(setRecent).catch(() => setRecent([])); }, [turns.length, busy]);

  async function ask(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setTurns((t) => [...t, { question, pending: true }]);
    try {
      const response = await api<AgentResponse>("/api/copilot/ask", { method: "POST", body: JSON.stringify({ question, conversation_id: conversationId }) });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { question, response } : x)));
    } catch (e) {
      const msg = e instanceof ApiError ? (e.status === 429 ? "Rate limit reached — wait a minute and retry." : e.message) : String(e);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { question, error: msg } : x)));
    } finally {
      setBusy(false);
    }
  }

  async function openRun(id: string) {
    const run = await api<AgentRun>(`/api/agent-runs/${id}`);
    if (run.response) setTurns([{ question: run.question, response: { ...run.response, run_id: run.run_id, duration_ms: run.duration_ms ?? 0, provider: run.provider ?? "" } }]);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
      <div className="flex min-h-[calc(100vh-8rem)] flex-col">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">AI Copilot</h1>
            <p className="text-sm text-slate-500">Answers come from deterministic tools over your telemetry; every claim links to evidence.</p>
          </div>
          {turns.length > 0 && <Button variant="secondary" onClick={() => { setTurns([]); setConversationId(newId()); }}>New conversation</Button>}
        </div>

        <div className="flex-1 space-y-4">
          {turns.length === 0 && (
            <Card className="p-6">
              <div className="mb-4 flex items-center gap-2 text-sm font-medium text-slate-700"><Sparkles className="h-4 w-4 text-brand-600" /> Suggested questions</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {SUGGESTED.map((q) => (
                  <button key={q} onClick={() => ask(q)} className="rounded-lg border border-slate-200 px-3 py-2 text-left text-sm text-slate-700 hover:border-brand-300 hover:bg-brand-50">{q}</button>
                ))}
              </div>
              <p className="mt-4 text-xs text-slate-500">Actions you request are only <em>proposed</em>. An administrator must approve them in the Approval Center before anything executes.</p>
            </Card>
          )}
          {turns.map((t, i) => (
            <div key={i} className="space-y-3">
              <div className="flex justify-end">
                <div className="flex max-w-[85%] items-start gap-2 rounded-2xl rounded-tr-sm bg-brand-600 px-4 py-2.5 text-sm text-white">
                  {t.question}<User className="mt-0.5 h-4 w-4 shrink-0 opacity-70" />
                </div>
              </div>
              <Card className="p-4">
                <div className="flex gap-3">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-900"><Bot className="h-4 w-4 text-white" /></div>
                  <div className="min-w-0 flex-1">
                    {t.pending && <p className="animate-pulse text-sm text-slate-500">Classifying intent → planning → running tools → validating evidence…</p>}
                    {t.error && <p className="text-sm text-rose-700">Request failed: {t.error}</p>}
                    {t.response && <AnswerCard r={t.response} />}
                  </div>
                </div>
              </Card>
            </div>
          ))}
          <div ref={end} />
        </div>

        <form className="sticky bottom-0 mt-4 flex gap-2 border-t border-slate-200 bg-slate-50 pb-2 pt-3" onSubmit={(e) => { e.preventDefault(); void ask(input); }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} maxLength={2000} disabled={busy}
            placeholder="Ask about devices, compliance, risks — or request an action…" aria-label="Question"
            className="flex-1 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20" />
          <Button type="submit" loading={busy} disabled={!input.trim()}><Send className="h-4 w-4" /> Ask</Button>
        </form>
      </div>

      <aside className="hidden lg:block">
        <Card>
          <div className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-900">Recent requests</div>
          <ul className="max-h-[70vh] divide-y divide-slate-100 overflow-auto">
            {recent.length === 0 && <li className="px-4 py-6 text-center text-xs text-slate-500">No history yet</li>}
            {recent.map((r) => (
              <li key={r.run_id}>
                <button onClick={() => openRun(r.run_id)} className="w-full px-4 py-2.5 text-left hover:bg-slate-50">
                  <p className="line-clamp-2 text-xs text-slate-700">{r.question}</p>
                  <p className="mt-1 text-[11px] text-slate-400">{humanize(r.status)} · {new Date(r.created_at).toLocaleTimeString()}</p>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </aside>
    </div>
  );
}
