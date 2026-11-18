const TOKEN_KEY = "fleetpilot.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session-only */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

function messageFrom(detail: unknown): { message: string; code?: string } {
  if (!detail) return { message: "Request failed" };
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) return { message: detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ") };
  const d = detail as { message?: string; code?: string };
  return { message: d.message ?? JSON.stringify(detail), code: d.code };
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    let body: { detail?: unknown } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error */
    }
    const { message, code } = messageFrom(body.detail);
    throw new ApiError(res.status, message, code);
  }
  return res.json() as Promise<T>;
}

export async function downloadReport() {
  const token = getToken();
  const res = await fetch("/api/reports/fleet-health.pdf", { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new ApiError(res.status, "Report generation failed");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "fleet-health-report.pdf";
  a.click();
  URL.revokeObjectURL(url);
}
