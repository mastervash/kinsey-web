import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import StatusPill from "../components/StatusPill";
import { useToast } from "../toast";
import type { Search } from "../types";
import { fmtDate, relTime } from "../util";

export default function History() {
  const toast = useToast();
  const [items, setItems] = useState<Search[] | null>(null);
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(50);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .listSearches(limit)
      .then((r) => {
        setItems(Array.isArray(r) ? r : []);
        setErr(null);
      })
      .catch((e) => {
        setErr(e.message);
        setItems([]);
        toast.error(e);
      });
  }, [limit, toast]);

  useEffect(load, [load]);

  const del = async (s: Search) => {
    if (!confirm(`Delete search "${s.query}" and all its results?`)) return;
    const prev = items;
    setItems((it) => (it ? it.filter((x) => x.id !== s.id) : it));
    try {
      await api.deleteSearch(s.id);
      toast.push("Search deleted", "ok");
    } catch (e) {
      setItems(prev);
      toast.error(e);
    }
  };

  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return (items || []).filter((s) => !ql || s.query.toLowerCase().includes(ql) || s.kind.includes(ql));
  }, [items, q]);

  return (
    <div className="page">
      <div className="page-head">
        <h2>History</h2>
        <div className="row gap">
          <input className="input-sm" placeholder="filter…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn btn-ghost btn-sm" onClick={load}>
            Refresh
          </button>
        </div>
      </div>
      {items === null ? (
        <div className="card empty">Loading…</div>
      ) : shown.length === 0 ? (
        <div className="card empty">
          {err ? <p className="bad-text">{err}</p> : <p className="muted">No searches yet.</p>}
          <Link to="/" className="btn btn-primary">
            New search
          </Link>
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Query</th>
                <th>Status</th>
                <th className="num">Checked</th>
                <th className="num">Claimed</th>
                <th className="num">Uncertain</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.id}>
                  <td>
                    <span className={`kind kind-${s.kind}`}>{s.kind}</span>
                  </td>
                  <td>
                    <Link to={`/search/${encodeURIComponent(s.id)}`} className="link-strong">
                      {s.query}
                    </Link>
                  </td>
                  <td>
                    <StatusPill status={s.status} />
                  </td>
                  <td className="num">
                    {s.counts?.checked ?? 0}/{s.counts?.total ?? 0}
                  </td>
                  <td className="num good-text">{s.counts?.claimed ?? 0}</td>
                  <td className="num warn-text">{s.counts?.uncertain ?? 0}</td>
                  <td title={fmtDate(s.created_at)} className="muted">
                    {relTime(s.created_at)}
                  </td>
                  <td className="num">
                    <button className="btn btn-danger btn-sm" onClick={() => del(s)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length >= limit && (
            <div className="row-center">
              <button className="btn btn-ghost btn-sm" onClick={() => setLimit((l) => l + 50)}>
                Load more
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
