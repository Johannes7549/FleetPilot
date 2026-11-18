"use client";

import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Button } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

/** Creates a pending proposal through the same server-side validation the agent uses. */
export function ProposeButton({ tool, args }: { tool: string; args: Record<string, unknown> }) {
  const { can } = useAuth();
  const [state, setState] = useState<{ busy?: boolean; msg?: string; err?: string }>({});
  if (!can("actions:propose")) return <span className="text-xs text-slate-500">Your role cannot propose actions</span>;
  if (state.msg) return <span className="text-xs font-medium text-emerald-700">{state.msg}</span>;
  return (
    <span className="flex items-center gap-2">
      {state.err && <span className="text-xs text-rose-700">{state.err}</span>}
      <Button variant="secondary" className="px-2.5 py-1 text-xs" loading={state.busy} onClick={async () => {
        setState({ busy: true });
        try {
          const r = await api<{ proposal_id: string; created: boolean }>("/api/actions", { method: "POST", body: JSON.stringify({ action_type: tool, params: args }) });
          setState({ msg: `${r.created ? "Proposed" : "Already pending"} · ${r.proposal_id.slice(0, 8)} — awaiting approval` });
        } catch (e) {
          setState({ err: e instanceof ApiError ? e.message : String(e) });
        }
      }}>Propose</Button>
    </span>
  );
}
