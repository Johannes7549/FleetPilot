"use client";

import { ModeBadge } from "@/components/ExecutionMode";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Activity, Bot, CheckSquare, LayoutDashboard, Lightbulb, LogOut, Menu, Monitor, Plane, Settings, X } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";
import { Badge, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { DemoUser } from "@/types/api";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/copilot", label: "AI Copilot", icon: Bot },
  { href: "/devices", label: "Devices", icon: Monitor },
  { href: "/insights", label: "Insights", icon: Lightbulb },
  { href: "/actions", label: "Actions", icon: CheckSquare },
  { href: "/activity", label: "Agent Activity", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings },
];

function UserSwitcher() {
  const { me, login } = useAuth();
  const [users, setUsers] = useState<DemoUser[]>([]);
  useEffect(() => {
    api<DemoUser[]>("/api/auth/users").then(setUsers).catch(() => setUsers([]));
  }, []);
  if (!me || users.length === 0) return null;
  return (
    <label className="flex items-center gap-2 text-xs text-slate-500">
      <span className="hidden sm:inline">Signed in as</span>
      <select
        aria-label="Switch demo user"
        className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-800"
        value={me.user_id}
        onChange={async (e) => {
          await login(e.target.value);
          window.location.reload();
        }}
      >
        {users.map((u) => (
          <option key={u.username} value={u.username}>
            {u.display_name} ({u.role})
          </option>
        ))}
      </select>
    </label>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me, loading, logout } = useAuth();
  const [open, setOpen] = useState(false);

  if (pathname === "/login") return <>{children}</>;
  if (loading || !me) return <Spinner label="Signing in…" />;

  const nav = (
    <nav className="flex flex-1 flex-col gap-1 px-3 py-4">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link key={href} href={href} onClick={() => setOpen(false)}
            className={cn("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5 hover:text-white")}>
            <Icon className="h-4 w-4" /> {label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className={cn("fixed inset-y-0 left-0 z-40 flex w-60 flex-col bg-slate-900 transition-transform lg:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex h-16 items-center gap-2 border-b border-white/10 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-500"><Plane className="h-4 w-4 text-white" /></div>
          <div>
            <p className="text-sm font-semibold text-white">FleetPilot</p>
            <p className="text-[11px] text-slate-400">Fleet operations copilot</p>
          </div>
          <button className="ml-auto text-slate-400 lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
        </div>
        {nav}
        <div className="border-t border-white/10 p-4 text-xs text-slate-400">
          <p className="font-medium text-slate-200">{me.company.name}</p>
          <p>Tenant {me.company.id}</p>
          <button onClick={logout} className="mt-3 flex items-center gap-1.5 text-slate-300 hover:text-white"><LogOut className="h-3.5 w-3.5" /> Sign out</button>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-slate-900/40 lg:hidden" onClick={() => setOpen(false)} />}
      <div className="flex min-w-0 flex-1 flex-col lg:pl-60">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur sm:px-6">
          <button className="text-slate-600 lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-800">{me.company.name}</span>
            <Badge tone={me.role === "admin" ? "brand" : "neutral"}>{me.role}</Badge>
          </div>
          <div className="ml-auto flex items-center gap-3"><ModeBadge /><UserSwitcher /></div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
