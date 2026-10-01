import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Toggle } from "../components/OptionsPanel";
import StatusPill from "../components/StatusPill";
import { useDebounced, useTags } from "../hooks";
import { useToast } from "../toast";
import type { Result, SiteDetail, SiteRow, SiteTestResult } from "../types";
import { confClass, favicon, fmtDate, relTime } from "../util";

const PAGE = 50;

export default function Sites() {
  const toast = useToast();
  const tags = useTags();
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 300);
  const [tag, setTag] = useState("");
  const [state, setState] = useState("");
  const [sort, setSort] = useState("rank");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; items: SiteRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => setOffset(0), [dq, tag, state, sort]);

  const load = useCallback(() => {
    setLoading(true);
    api
      .sites({ q: dq, tag, state, sort, offset, limit: PAGE })
      .then((d) => setData({ total: d?.total ?? 0, items: d?.items ?? [] }))
      .catch((e) => {
        toast.error(e);
        setData((d) => d ?? { total: 0, items: [] });
      })
      .finally(() => setLoading(false));
  }, [dq, tag, state, sort, offset, toast]);
  useEffect(load, [load]);

  const onRowUpdate = (row: SiteRow) =>
    setData((d) => (d ? { ...d, items: d.items.map((x) => (x.name === row.name ? { ...x, ...row } : x)) } : d));

  const total = data?.total ?? 0;
  const page = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div className="page">
      <div className="page-head">
        <h2>
          Sites <span className="muted">{total.toLocaleString()}</span>
        </h2>
      </div>
      <section className="card filters">
        <input className="input-sm grow" placeholder="search sites…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input-sm" value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">all tags</option>
          {tags.map((t) => (
            <option key={t.tag} value={t.tag}>
              {t.tag} ({t.count})
            </option>
          ))}
        </select>
        <select className="input-sm" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">any state</option>
          <option value="enabled">enabled</option>
          <option value="disabled">disabled</option>
          <option value="quarantined">quarantined</option>
        </select>
        <select className="input-sm" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="rank">sort: rank</option>
          <option value="name">sort: name</option>
          <option value="reliability">sort: reliability</option>
        </select>
        {loading && <span className="muted small">loading…</span>}
      </section>

      <div className="card table-wrap">
        <table className="table table-click">
          <thead>
            <tr>
              <th>Site</th>
              <th>Tags</th>
              <th>Check</th>
              <th className="num">Rank</th>
              <th className="num">Reliab.</th>
              <th className="num">FP / ✓</th>
              <th>Verified</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items || []).map((s) => (
              <tr key={s.name} onClick={() => setSelected(s.name)} className={selected === s.name ? "selected" : ""}>
                <td>
                  <div className="site-cell">
                    <img className="favicon" src={favicon(s.url_main)} alt="" width={16} height={16} loading="lazy" onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
                    <span className="link-strong">{s.name}</span>
                    {s.source !== "maigret" && <span className="chip chip-dim">{s.source}</span>}
                  </div>
                </td>
                <td className="tags-cell">
                  {(s.tags || []).slice(0, 3).map((t) => (
                    <span key={t} className="chip chip-tag">
                      {t}
                    </span>
                  ))}
                  {(s.tags || []).length > 3 && <span className="muted small"> +{s.tags.length - 3}</span>}
                </td>
                <td className="muted">{s.check_type}</td>
                <td className="num">{s.rank || "—"}</td>
                <td className="num">
                  {s.reliability == null ? (
                    <span className="muted">—</span>
                  ) : (
                    <span className={confClass(s.reliability <= 1 ? s.reliability * 100 : s.reliability) + "-text"}>
                      {(s.reliability <= 1 ? s.reliability * 100 : s.reliability).toFixed(0)}
                    </span>
                  )}
                </td>
                <td className="num">
                  <span className={s.fp_reports ? "bad-text" : "muted"}>{s.fp_reports}</span> /{" "}
                  <span className={s.confirmations ? "good-text" : "muted"}>{s.confirmations}</span>
                </td>
                <td className="muted" title={fmtDate(s.last_verified)}>
                  {s.last_verified ? relTime(s.last_verified) : "never"}
                </td>
                <td>
                  <SiteState s={s} />
                </td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={8} className="empty muted">
                  No sites match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          ‹ Prev
        </button>
        <span className="muted small">
          page {page} / {pages}
        </span>
        <button className="btn btn-ghost btn-sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
          Next ›
        </button>
      </div>

      {selected && <SiteDrawer name={selected} onClose={() => setSelected(null)} onUpdate={onRowUpdate} />}
    </div>
  );
}

function SiteState({ s }: { s: SiteRow }) {
  if (s.quarantined) return <span className="pill pill-blocked">quarantined</span>;
  if (s.disabled) return <span className="pill pill-stopped">disabled</span>;
  return <span className="pill pill-claimed">enabled</span>;
}

function SiteDrawer({ name, onClose, onUpdate }: { name: string; onClose: () => void; onUpdate: (r: SiteRow) => void }) {
  const toast = useToast();
  const [site, setSite] = useState<SiteDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [testUser, setTestUser] = useState("");
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<SiteTestResult | null>(null);

  useEffect(() => {
    setSite(null);
    setTest(null);
    api.site(name).then(setSite).catch((e) => toast.error(e));
  }, [name, toast]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const toggle = async (enabled: boolean) => {
    if (!site) return;
    const prev = site;
    setSite({ ...site, disabled: !enabled });
    setBusy(true);
    try {
      const row = await api.patchSite(name, { disabled: !enabled });
      setSite((s) => (s ? { ...s, ...row } : s));
      onUpdate(row);
      toast.push(`${name} ${enabled ? "enabled" : "disabled"}`, "ok");
    } catch (e) {
      setSite(prev);
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.testSite(name, testUser.trim() || undefined));
    } catch (e) {
      toast.error(e);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={`Site ${name}`}>
        <header className="drawer-head">
          <img className="favicon" src={favicon(site?.url_main || "")} alt="" width={20} height={20} onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
          <h3>{name}</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        {!site ? (
          <div className="muted">Loading…</div>
        ) : (
          <div className="drawer-body">
            <div className="kv-grid">
              <span>URL</span>
              <a href={site.url_main} target="_blank" rel="noreferrer noopener">
                {site.url_main}
              </a>
              <span>Profile</span>
              <code>{site.url}</code>
              <span>Check</span>
              <span>{site.check_type}</span>
              <span>Source</span>
              <span>{site.source}</span>
              <span>Rank</span>
              <span>{site.rank || "—"}</span>
              <span>Feedback</span>
              <span>
                <span className="bad-text">{site.fp_reports} FP</span> · <span className="good-text">{site.confirmations} confirmed</span>
              </span>
              <span>Verified</span>
              <span>{fmtDate(site.last_verified)}</span>
              <span>State</span>
              <span>
                <SiteState s={site} />
              </span>
            </div>
            <Toggle label="Enabled" checked={!site.disabled} disabled={busy} onChange={toggle} />

            <div className="section-label">Health test</div>
            <div className="row gap">
              <input className="input-sm grow" placeholder="known username (optional)" value={testUser} onChange={(e) => setTestUser(e.target.value)} />
              <button className="btn btn-primary btn-sm" onClick={runTest} disabled={testing}>
                {testing ? "Testing…" : "Test"}
              </button>
            </div>
            {test && (
              <div className={`test-result ${test.healthy ? "healthy" : "unhealthy"}`}>
                <div className="test-verdict">{test.healthy ? "✓ HEALTHY" : "✗ UNHEALTHY"}</div>
                <TestLine label="claimed" r={test.claimed} />
                <TestLine label="unclaimed" r={test.unclaimed} />
              </div>
            )}

            <div className="section-label">Raw</div>
            <pre className="raw-json">{JSON.stringify(site.raw ?? site, null, 2)}</pre>
          </div>
        )}
      </aside>
    </div>
  );
}

function TestLine({ label, r }: { label: string; r: Result | undefined }) {
  if (!r) return null;
  return (
    <div className="test-line">
      <span className="muted">{label}</span>
      <a href={r.url} target="_blank" rel="noreferrer noopener">
        {r.username}
      </a>
      <StatusPill status={r.status} />
      <span className={`conf-badge sm ${confClass(r.confidence)}`}>{Math.round(r.confidence)}</span>
      {r.http_status != null && <span className="muted small">HTTP {r.http_status}</span>}
    </div>
  );
}
