import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, downloadExport } from "../api";
import CandidatesPanel from "../components/CandidatesPanel";
import GraphView from "../components/GraphView";
import ResultCard from "../components/ResultCard";
import StatusPill from "../components/StatusPill";
import { useToast } from "../toast";
import type { Candidate, Counts, GraphData, Result, Search, SearchStatus, Verdict } from "../types";
import { fmtDate } from "../util";

const STATUS_FILTERS = ["claimed", "uncertain", "blocked", "unknown"] as const;
type SF = (typeof STATUS_FILTERS)[number];
const ACTIVE: SearchStatus[] = ["queued", "running"];

export default function SearchDetail() {
  const { id = "" } = useParams();
  const toast = useToast();
  const [search, setSearch] = useState<Search | null>(null);
  const [results, setResults] = useState<Map<string, Result>>(new Map());
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [progress, setProgress] = useState<{ checked: number; total: number }>({ checked: 0, total: 0 });
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<"idle" | "live" | "reconnecting" | "closed">("idle");
  const [stopping, setStopping] = useState(false);

  // filters
  const [minConf, setMinConf] = useState(60);
  const [statuses, setStatuses] = useState<Set<SF>>(new Set(["claimed", "uncertain"]));
  const [text, setText] = useState("");
  const [tag, setTag] = useState("");
  const [sort, setSort] = useState<"confidence" | "site">("confidence");
  const [hideFp, setHideFp] = useState(false);
  const [mode, setMode] = useState<"grid" | "graph">("grid");
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);

  // batch incoming SSE results to avoid re-render storms
  const pending = useRef<Result[]>([]);
  const flushT = useRef<number | null>(null);
  const flush = useCallback(() => {
    flushT.current = null;
    const batch = pending.current;
    pending.current = [];
    if (!batch.length) return;
    setResults((prev) => {
      const m = new Map(prev);
      for (const r of batch) {
        const old = m.get(r.id);
        // keep local feedback if server replay lacks it
        m.set(r.id, old && old.feedback && !r.feedback ? { ...r, feedback: old.feedback } : r);
      }
      return m;
    });
  }, []);
  const enqueue = useCallback(
    (r: Result) => {
      pending.current.push(r);
      if (flushT.current == null) flushT.current = window.setTimeout(flush, 150);
    },
    [flush],
  );

  // initial load
  useEffect(() => {
    let alive = true;
    setSearch(null);
    setResults(new Map());
    setGraph(null);
    setLoadErr(null);
    api
      .getSearch(id)
      .then(({ search, results }) => {
        if (!alive) return;
        setSearch(search);
        setCandidates(search.candidates || []);
        setProgress({ checked: search.counts?.checked ?? 0, total: search.counts?.total ?? 0 });
        setResults((prev) => {
          const m = new Map(prev);
          for (const r of results || []) if (!m.has(r.id)) m.set(r.id, r);
          return m;
        });
      })
      .catch((e) => {
        if (!alive) return;
        setLoadErr(e.message);
        toast.error(e);
      });
    return () => {
      alive = false;
    };
  }, [id, toast]);

  const isActive = !!search && ACTIVE.includes(search.status);

  // SSE stream while active
  useEffect(() => {
    if (!search || !ACTIVE.includes(search.status)) return;
    const es = new EventSource(api.eventsUrl(id));
    let finished = false;
    setStreamState("live");
    const parse = (e: MessageEvent) => {
      try {
        return JSON.parse(e.data);
      } catch {
        return null;
      }
    };
    es.addEventListener("open", () => setStreamState("live"));
    es.addEventListener("start", (e) => {
      const d = parse(e as MessageEvent);
      if (d?.total != null) setProgress((p) => ({ ...p, total: d.total }));
      setSearch((s) => (s && s.status === "queued" ? { ...s, status: "running" } : s));
    });
    es.addEventListener("candidates", (e) => {
      const d = parse(e as MessageEvent);
      if (Array.isArray(d?.candidates)) setCandidates(d.candidates);
    });
    es.addEventListener("progress", (e) => {
      const d = parse(e as MessageEvent);
      if (d) setProgress({ checked: d.checked ?? 0, total: d.total ?? 0 });
    });
    es.addEventListener("result", (e) => {
      const d = parse(e as MessageEvent) as Result | null;
      if (d?.id) enqueue(d);
    });
    es.addEventListener("done", (e) => {
      const d = parse(e as MessageEvent) as { status: SearchStatus; counts: Counts } | null;
      finished = true;
      es.close();
      flush();
      setStreamState("closed");
      setSearch((s) =>
        s ? { ...s, status: d?.status ?? "done", counts: d?.counts ?? s.counts, finished_at: s.finished_at ?? new Date().toISOString() } : s,
      );
      if (d?.counts) setProgress({ checked: d.counts.checked, total: d.counts.total });
      setGraph(null);
    });
    es.addEventListener("error", (e) => {
      const me = e as MessageEvent;
      if (me.data) {
        const d = parse(me);
        toast.push(`Search error: ${d?.message ?? "unknown"}`, "error");
        finished = true;
        es.close();
        setStreamState("closed");
        setSearch((s) => (s ? { ...s, status: "error" } : s));
        return;
      }
      // transport error; EventSource reconnects automatically
      if (!finished) setStreamState(es.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    });
    return () => {
      es.close();
      if (flushT.current != null) {
        clearTimeout(flushT.current);
        flushT.current = null;
      }
      flush();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, search?.id, isActive]);

  // graph fetch
  useEffect(() => {
    if (mode !== "graph" || graph || !search) return;
    setGraphLoading(true);
    api
      .graph(id)
      .then((g) => setGraph({ nodes: g?.nodes || [], edges: g?.edges || [] }))
      .catch((e) => toast.error(e))
      .finally(() => setGraphLoading(false));
  }, [mode, graph, id, search, toast]);

  const onFeedback = useCallback(
    async (r: Result, v: Verdict) => {
      const prev = r.feedback;
      setResults((m) => {
        const n = new Map(m);
        const cur = n.get(r.id);
        if (cur) n.set(r.id, { ...cur, feedback: v });
        return n;
      });
      try {
        await api.feedback(r.id, v);
      } catch (e) {
        setResults((m) => {
          const n = new Map(m);
          const cur = n.get(r.id);
          if (cur) n.set(r.id, { ...cur, feedback: prev });
          return n;
        });
        toast.error(e);
      }
    },
    [toast],
  );

  const stop = async () => {
    setStopping(true);
    try {
      await api.stopSearch(id);
      toast.push("Stop requested", "info");
    } catch (e) {
      toast.error(e);
    } finally {
      setStopping(false);
    }
  };

  const doExport = async (f: "json" | "csv" | "html") => {
    try {
      await downloadExport(id, f);
    } catch (e) {
      toast.error(e);
    }
  };

  const all = useMemo(() => Array.from(results.values()), [results]);
  const allTags = useMemo(() => {
    const c = new Map<string, number>();
    all.forEach((r) => (r.tags || []).forEach((t) => c.set(t, (c.get(t) || 0) + 1)));
    return Array.from(c.entries()).sort((a, b) => b[1] - a[1]);
  }, [all]);
  const statusCounts = useMemo(() => {
    const c: Record<string, number> = {};
    all.forEach((r) => (c[r.status] = (c[r.status] || 0) + 1));
    return c;
  }, [all]);

  const shown = useMemo(() => {
    const tl = text.trim().toLowerCase();
    const out = all.filter((r) => {
      if (r.confidence < minConf) return false;
      if (statuses.size && !statuses.has(r.status as SF)) return false;
      if (hideFp && r.feedback === "false_positive") return false;
      if (tag && !(r.tags || []).includes(tag)) return false;
      if (tl) {
        const hay = `${r.site} ${r.username} ${r.url} ${Object.values(r.ids || {}).join(" ")}`.toLowerCase();
        if (!hay.includes(tl)) return false;
      }
      return true;
    });
    out.sort((a, b) =>
      sort === "site" ? a.site.localeCompare(b.site) : b.confidence - a.confidence || a.site.localeCompare(b.site),
    );
    return out;
  }, [all, minConf, statuses, hideFp, tag, text, sort]);

  if (loadErr && !search) {
    return (
      <div className="page">
        <div className="card empty">
          <h3>Could not load search</h3>
          <p className="bad-text">{loadErr}</p>
          <Link to="/history" className="btn btn-ghost">
            Back to history
          </Link>
        </div>
      </div>
    );
  }
  if (!search) return <div className="page"><div className="card empty">Loading…</div></div>;

  const pct = progress.total ? Math.min(100, (progress.checked / progress.total) * 100) : search.status === "done" ? 100 : 0;
  const claimed = all.filter((r) => r.status === "claimed").length;

  return (
    <div className="page">
      <section className="card detail-head">
        <div className="detail-title">
          <span className={`kind kind-${search.kind}`}>{search.kind}</span>
          <h2>{search.query}</h2>
          <StatusPill status={search.status} />
          {isActive && (
            <span className={`stream stream-${streamState}`} title="Live stream state">
              {streamState === "live" ? "● live" : streamState === "reconnecting" ? "◌ reconnecting" : ""}
            </span>
          )}
        </div>
        <div className="detail-actions">
          {isActive && (
            <button className="btn btn-danger btn-sm" onClick={stop} disabled={stopping}>
              {stopping ? "Stopping…" : "■ Stop"}
            </button>
          )}
          <div className="btn-group">
            {(["json", "csv", "html"] as const).map((f) => (
              <button key={f} className="btn btn-ghost btn-sm" onClick={() => doExport(f)}>
                {f.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <div className="progress">
          <div className={`progress-fill ${isActive ? "is-active" : ""}`} style={{ width: `${pct}%` }} />
        </div>
        <div className="detail-meta muted small">
          <span>
            {progress.checked.toLocaleString()} / {progress.total.toLocaleString()} checked ({pct.toFixed(0)}%)
          </span>
          <span className="good-text">{claimed} claimed</span>
          <span className="warn-text">{statusCounts.uncertain || 0} uncertain</span>
          <span>{all.length} results</span>
          <span>started {fmtDate(search.created_at)}</span>
          {search.finished_at && <span>finished {fmtDate(search.finished_at)}</span>}
          {search.context && Object.keys(search.context).length > 0 && (
            <span>
              context:{" "}
              {Object.entries(search.context)
                .filter(([, v]) => v && (!Array.isArray(v) || v.length))
                .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("|") : v}`)
                .join(", ")}
            </span>
          )}
        </div>
      </section>

      {search.kind !== "username" && <CandidatesPanel candidates={candidates} />}

      <section className="card filters">
        <div className="seg">
          <button className={mode === "grid" ? "active" : ""} onClick={() => setMode("grid")}>
            Grid
          </button>
          <button className={mode === "graph" ? "active" : ""} onClick={() => setMode("graph")}>
            Graph
          </button>
        </div>
        <label className="filter-conf">
          <span>
            ≥ <b className="accent">{minConf}</b>
          </span>
          <input type="range" min={0} max={100} value={minConf} onChange={(e) => setMinConf(Number(e.target.value))} />
        </label>
        <div className="status-filters">
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              className={`chip chip-toggle pill-${s} ${statuses.has(s) ? "on" : ""}`}
              onClick={() =>
                setStatuses((cur) => {
                  const n = new Set(cur);
                  if (n.has(s)) n.delete(s);
                  else n.add(s);
                  return n;
                })
              }
            >
              {s} <span className="muted">{statusCounts[s] || 0}</span>
            </button>
          ))}
        </div>
        <input className="input-sm" placeholder="filter text…" value={text} onChange={(e) => setText(e.target.value)} />
        <select className="input-sm" value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">all tags</option>
          {allTags.map(([t, c]) => (
            <option key={t} value={t}>
              {t} ({c})
            </option>
          ))}
        </select>
        <select className="input-sm" value={sort} onChange={(e) => setSort(e.target.value as "confidence" | "site")}>
          <option value="confidence">sort: confidence</option>
          <option value="site">sort: site</option>
        </select>
        <label className="mini-check">
          <input type="checkbox" checked={hideFp} onChange={(e) => setHideFp(e.target.checked)} /> hide FP
        </label>
        <span className="muted small filter-count">
          {shown.length} / {all.length}
        </span>
      </section>

      {mode === "graph" ? (
        graphLoading && !graph ? (
          <div className="card empty">Loading graph…</div>
        ) : graph ? (
          <>
            {isActive && (
              <div className="row-end">
                <button className="btn btn-ghost btn-sm" onClick={() => setGraph(null)}>
                  Refresh graph
                </button>
              </div>
            )}
            <GraphView data={graph} />
          </>
        ) : (
          <div className="card empty muted">Graph unavailable.</div>
        )
      ) : shown.length === 0 ? (
        <div className="card empty muted">
          {isActive ? "Scanning… results will stream in." : all.length ? "No results match the filters." : "No results."}
        </div>
      ) : (
        <div className="results-grid">
          {shown.map((r) => (
            <ResultCard key={r.id} r={r} onFeedback={onFeedback} />
          ))}
        </div>
      )}
    </div>
  );
}
