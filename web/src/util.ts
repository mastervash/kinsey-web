export function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url.replace(/^https?:\/\//, "").split("/")[0] || "";
  }
}

export function favicon(url: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(hostOf(url))}&sz=32`;
}

export function confClass(c: number): string {
  if (c >= 80) return "conf-high";
  if (c >= 60) return "conf-mid";
  return "conf-low";
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString();
}

export function relTime(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s).getTime();
  if (isNaN(d)) return s;
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return `${Math.max(0, Math.floor(diff))}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export function isHttpUrl(s: string | undefined | null): s is string {
  return !!s && /^https?:\/\//i.test(s);
}
