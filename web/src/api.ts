import type {
  GraphData, Health, Kind, Result, Search, SearchContext, SearchOptions, Settings,
  SiteDetail, SiteRow, SiteTestResult, Stats, TagCount, Verdict,
} from "./types";

const TOKEN_KEY = "mw_token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}
export function setToken(t: string) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;
export function setUnauthorizedHandler(h: UnauthorizedHandler | null) {
  onUnauthorized = h;
}

function extractMessage(body: unknown): string | null {
  if (!body) return null;
  if (typeof body === "string") return body;
  if (typeof body === "object") {
    const o = body as Record<string, unknown>;
    for (const k of ["detail", "message", "error"]) {
      const v = o[k];
      if (typeof v === "string") return v;
      if (Array.isArray(v)) return v.map((x) => extractMessage(x) ?? JSON.stringify(x)).join("; ");
      if (v && typeof v === "object") {
        const inner = extractMessage(v);
        if (inner) return inner;
      }
    }
    if (typeof o.msg === "string") return o.msg;
  }
  return null;
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Backend unreachable");
  }
  if (res.status === 401) {
    onUnauthorized?.();
    throw new ApiError(401, "Unauthorized: set API token in Settings");
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const msg = extractMessage(data) || `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function withToken(url: string): string {
  const t = getToken();
  if (!t) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

const enc = encodeURIComponent;

export const api = {
  health: () => request<Health>("GET", "/api/health"),
  stats: () => request<Stats>("GET", "/api/stats"),
  tags: () => request<TagCount[]>("GET", "/api/tags"),
  createSearch: (body: { kind: Kind; query: string; context?: SearchContext; options?: Partial<SearchOptions> }) =>
    request<Search>("POST", "/api/searches", body),
  listSearches: (limit = 50) => request<Search[]>("GET", `/api/searches${qs({ limit })}`),
  getSearch: (id: string) => request<{ search: Search; results: Result[] }>("GET", `/api/searches/${enc(id)}`),
  deleteSearch: (id: string) => request<{ ok: boolean }>("DELETE", `/api/searches/${enc(id)}`),
  stopSearch: (id: string) => request<{ ok: boolean }>("POST", `/api/searches/${enc(id)}/stop`),
  graph: (id: string) => request<GraphData>("GET", `/api/searches/${enc(id)}/graph`),
  exportUrl: (id: string, format: "json" | "csv" | "html") =>
    withToken(`/api/searches/${enc(id)}/export?format=${format}`),
  eventsUrl: (id: string) => withToken(`/api/searches/${enc(id)}/events`),
  feedback: (result_id: string, verdict: Verdict) =>
    request<{ ok: boolean }>("POST", "/api/results/feedback", { result_id, verdict }),
  sites: (p: { q?: string; tag?: string; state?: string; sort?: string; offset?: number; limit?: number }) =>
    request<{ total: number; items: SiteRow[] }>("GET", `/api/sites${qs(p)}`),
  site: (name: string) => request<SiteDetail>("GET", `/api/sites/${enc(name)}`),
  patchSite: (name: string, body: Partial<Pick<SiteRow, "disabled" | "tags" | "check_type" | "url">> & Record<string, unknown>) =>
    request<SiteRow>("PATCH", `/api/sites/${enc(name)}`, body),
  testSite: (name: string, username?: string) =>
    request<SiteTestResult>("POST", `/api/sites/${enc(name)}/test`, username ? { username } : {}),
  getSettings: () => request<Settings>("GET", "/api/settings"),
  putSettings: (s: Settings) => request<Settings>("PUT", "/api/settings", s),
};

/** Download export with auth header (falls back to token query param). */
export async function downloadExport(id: string, format: "json" | "csv" | "html") {
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers["Authorization"] = `Bearer ${t}`;
  let res: Response;
  try {
    res = await fetch(api.exportUrl(id, format), { headers });
  } catch {
    throw new ApiError(0, "Backend unreachable");
  }
  if (res.status === 401) {
    onUnauthorized?.();
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) throw new ApiError(res.status, `Export failed (${res.status})`);
  const blob = await res.blob();
  let filename = `maigret-${id}.${format}`;
  const cd = res.headers.get("Content-Disposition");
  const m = cd && /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  if (m) filename = decodeURIComponent(m[1]);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
