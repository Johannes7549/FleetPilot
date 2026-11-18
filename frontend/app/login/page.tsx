"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plane } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";
import { Badge, Button, Card, ErrorState } from "@/components/ui";
import { api } from "@/lib/api";
import type { DemoUser } from "@/types/api";

const ROLE_HELP: Record<string, string> = {
  admin: "read · propose · approve · settings",
  operator: "read · propose actions",
  viewer: "read only",
};

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api<DemoUser[]>("/api/auth/users").then(setUsers).catch((e) => setErr(e.message));
  }, []);

  const companies = Array.from(new Set((users ?? []).map((u) => u.company_id)));
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4 py-10">
      <div className="w-full max-w-3xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-600"><Plane className="h-5 w-5 text-white" /></div>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Sign in to FleetPilot</h1>
            <p className="text-sm text-slate-500">Demo identities — each is bound to one tenant and one role by the backend.</p>
          </div>
        </div>
        {err && <ErrorState message={`Cannot reach the API: ${err}`} />}
        <div className="grid gap-4 md:grid-cols-3">
          {companies.map((cid) => (
            <Card key={cid} className="p-4">
              <p className="text-sm font-semibold text-slate-900">{users!.find((u) => u.company_id === cid)!.display_name.split(" ")[0]}</p>
              <p className="mb-3 font-mono text-xs text-slate-500">{cid}</p>
              <div className="space-y-2">
                {users!.filter((u) => u.company_id === cid).map((u) => (
                  <Button key={u.username} variant="secondary" className="w-full justify-between" loading={busy === u.username}
                    onClick={async () => { setBusy(u.username); await login(u.username); router.replace("/"); }}>
                    <span className="flex items-center gap-2"><Badge tone={u.role === "admin" ? "brand" : "neutral"}>{u.role}</Badge></span>
                    <span className="text-[11px] text-slate-500">{ROLE_HELP[u.role]}</span>
                  </Button>
                ))}
              </div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
