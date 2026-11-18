"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, getToken, setToken } from "@/lib/api";
import type { Me } from "@/types/api";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (username: string) => Promise<void>;
  logout: () => void;
  can: (permission: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      setMe(await api<Me>("/api/me"));
    } catch {
      setToken(null);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!loading && !me && pathname !== "/login") router.replace("/login");
  }, [loading, me, pathname, router]);

  const login = async (username: string) => {
    const res = await api<{ token: string }>("/api/auth/login", { method: "POST", body: JSON.stringify({ username }) });
    setToken(res.token);
    setLoading(true);
    await refresh();
  };

  const logout = () => {
    setToken(null);
    setMe(null);
    router.replace("/login");
  };

  return (
    <Ctx.Provider value={{ me, loading, login, logout, can: (p) => !!me?.permissions.includes(p) }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
