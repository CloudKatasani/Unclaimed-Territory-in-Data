import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext } from "react";

export type Json = any; // eslint-disable-line @typescript-eslint/no-explicit-any

export const UserContext = createContext<{ user: string; setUser: (u: string) => void }>({
  user: "alice",
  setUser: () => {},
});
export const useUser = () => useContext(UserContext);

export async function api<T = Json>(path: string, opts: { method?: string; body?: unknown; user?: string } = {}): Promise<T> {
  const sep = path.includes("?") ? "&" : "?";
  const url = `/api${path}${opts.user ? `${sep}user=${opts.user}` : ""}`;
  const res = await fetch(url, {
    method: opts.method ?? "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function useApi<T = Json>(key: unknown[], path: string | null, opts: { refetchInterval?: number } = {}) {
  return useQuery<T>({
    queryKey: key,
    queryFn: () => api<T>(path as string),
    enabled: path !== null,
    refetchInterval: opts.refetchInterval,
  });
}

export function useAction<TVars = void>(fn: (v: TVars) => Promise<Json>, invalidate: unknown[][] = []) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k }));
      qc.invalidateQueries();
    },
  });
}

export const fmt = (v: unknown): string => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
};

export const shortId = (id: string | null | undefined) => (id ? `${id.slice(0, 6)}…${id.slice(-4)}` : "—");
