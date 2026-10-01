import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import OptionsPanel from "../components/OptionsPanel";
import { useStats, useTags } from "../hooks";
import { useToast } from "../toast";
import { DEFAULT_OPTIONS, type Kind, type Search, type SearchContext, type SearchOptions } from "../types";
import { relTime } from "../util";
import StatusPill from "../components/StatusPill";

const TABS: { kind: Kind; label: string; placeholder: string }[] = [
  { kind: "username", label: "Username", placeholder: "e.g. soxoj" },
  { kind: "name", label: "Real name", placeholder: "e.g. Jane Doe" },
  { kind: "email", label: "Email", placeholder: "e.g. jane@example.com" },
];

export default function SearchPage() {
  const nav = useNavigate();
  const toast = useToast();
  const tags = useTags();
  const { stats } = useStats(60000);
  const [kind, setKind] = useState<Kind>("username");
  const [query, setQuery] = useState("");
  const [ctx, setCtx] = useState<{ location: string; employer: string; school: string; email: string; keywords: string }>({
    location: "",
    employer: "",
    school: "",
    email: "",
    keywords: "",
  });
  const [opts, setOpts] = useState<SearchOptions>(DEFAULT_OPTIONS);
  const [showAdv, setShowAdv] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recent, setRecent] = useState<Search[]>([]);

  useEffect(() => {
    api
      .getSettings()
      .then((s) => s?.defaults && setOpts({ ...DEFAULT_OPTIONS, ...s.defaults }))
      .catch(() => {});
    api
      .listSearches(6)
      .then((r) => setRecent(Array.isArray(r) ? r.slice(0, 6) : []))
      .catch(() => {});
  }, []);

  const tab = TABS.find((t) => t.kind === kind)!;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) {
      toast.push("Enter a query", "error");
      return;
    }
    if (kind === "email" && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(q)) {
      toast.push("That doesn't look like an email", "error");
      return;
    }
    let context: SearchContext | undefined;
    if (kind === "name") {
      context = {};
      if (ctx.location.trim()) context.location = ctx.location.trim();
      if (ctx.employer.trim()) context.employer = ctx.employer.trim();
      if (ctx.school.trim()) context.school = ctx.school.trim();
      if (ctx.email.trim()) context.email = ctx.email.trim();
      const kw = ctx.keywords
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (kw.length) context.keywords = kw;
    }
    setBusy(true);
    try {
      const s = await api.createSearch({ kind, query: q, context, options: opts });
      nav(`/search/${encodeURIComponent(s.id)}`);
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page page-search">
      <section className="hero">
        <h1 className="hero-title">
          Find the <span className="accent-red">person</span> behind the <span className="accent-purple">handle</span>
        </h1>
        <p className="muted">
          {stats ? `${stats.sites_enabled.toLocaleString()} sites armed · control-probe verified results` : "Username, real-name and email OSINT across thousands of sites"}
        </p>
      </section>

      <form className="card search-card" onSubmit={submit}>
        <div className="tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.kind}
              type="button"
              role="tab"
              aria-selected={kind === t.kind}
              className={`tab ${kind === t.kind ? "active" : ""}`}
              onClick={() => setKind(t.kind)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="search-row">
          <input
            className="search-input"
            autoFocus
            value={query}
            type={kind === "email" ? "email" : "text"}
            placeholder={tab.placeholder}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="btn btn-primary btn-lg" type="submit" disabled={busy}>
            {busy ? "Starting…" : "Hunt"}
          </button>
        </div>

        {kind === "name" && (
          <div className="context-grid">
            <div className="section-label">Context <small className="muted">(optional, improves candidate ranking)</small></div>
            {(["location", "employer", "school", "email"] as const).map((k) => (
              <label className="field" key={k}>
                <span>{k[0].toUpperCase() + k.slice(1)}</span>
                <input value={ctx[k]} onChange={(e) => setCtx({ ...ctx, [k]: e.target.value })} />
              </label>
            ))}
            <label className="field field-wide">
              <span>Keywords <small className="muted">(comma separated)</small></span>
              <input value={ctx.keywords} onChange={(e) => setCtx({ ...ctx, keywords: e.target.value })} placeholder="python, photography…" />
            </label>
          </div>
        )}

        <button type="button" className="adv-toggle" onClick={() => setShowAdv((v) => !v)} aria-expanded={showAdv}>
          <span className={`caret ${showAdv ? "open" : ""}`}>▸</span> Advanced options
          <span className="muted adv-summary">
            top {opts.top_sites || "all"} · {opts.timeout}s · ≥{opts.min_confidence}
            {opts.tags.length ? ` · ${opts.tags.length} tags` : ""}
            {opts.control_probe ? " · probe" : ""}
          </span>
        </button>
        {showAdv && (
          <div className="adv-drawer">
            <OptionsPanel value={opts} onChange={setOpts} tags={tags} kind={kind} />
            <div className="row-end">
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpts(DEFAULT_OPTIONS)}>
                Reset to built-in defaults
              </button>
            </div>
          </div>
        )}
      </form>

      {stats && (
        <section className="stat-tiles">
          <Stat label="Sites enabled" value={stats.sites_enabled} sub={`of ${stats.sites_total}`} />
          <Stat label="Quarantined" value={stats.sites_quarantined} tone={stats.sites_quarantined ? "warn" : undefined} />
          <Stat label="Searches" value={stats.searches} />
          <Stat label="Claimed hits" value={stats.results_claimed} tone="good" />
          <Stat label="Confirmed" value={stats.feedback_confirmed} />
          <Stat label="False positives" value={stats.feedback_fp} tone={stats.feedback_fp ? "bad" : undefined} />
        </section>
      )}

      {recent.length > 0 && (
        <section className="card">
          <div className="card-head">
            <h3>Recent</h3>
            <Link to="/history" className="btn btn-ghost btn-sm">
              All history
            </Link>
          </div>
          <div className="recent-list">
            {recent.map((s) => (
              <Link key={s.id} to={`/search/${encodeURIComponent(s.id)}`} className="recent-item">
                <span className={`kind kind-${s.kind}`}>{s.kind}</span>
                <span className="recent-q">{s.query}</span>
                <StatusPill status={s.status} />
                <span className="muted small">
                  {s.counts?.claimed ?? 0} hits · {relTime(s.created_at)}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: number; sub?: string; tone?: "good" | "warn" | "bad" }) {
  return (
    <div className={`card stat ${tone ? `stat-${tone}` : ""}`}>
      <div className="stat-value">{(value ?? 0).toLocaleString()}</div>
      <div className="stat-label">
        {label} {sub && <span className="muted">{sub}</span>}
      </div>
    </div>
  );
}
