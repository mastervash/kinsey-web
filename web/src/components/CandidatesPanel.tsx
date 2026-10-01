import { useState } from "react";
import type { Candidate } from "../types";
import { isHttpUrl } from "../util";

export default function CandidatesPanel({ candidates }: { candidates: Candidate[] }) {
  const [collapsed, setCollapsed] = useState(false);
  if (!candidates.length) return null;
  const max = Math.max(...candidates.map((c) => c.score), 1);
  return (
    <section className="card candidates">
      <div className="card-head">
        <h3>
          Candidates <span className="muted">({candidates.length})</span>
        </h3>
        <button className="btn btn-ghost btn-sm" onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? "Show" : "Hide"}
        </button>
      </div>
      {!collapsed && (
        <div className="cand-grid">
          {candidates.map((c) => (
            <div key={c.username} className="cand">
              <div className="cand-avatar">
                {isHttpUrl(c.avatar) ? (
                  <img src={c.avatar} alt="" referrerPolicy="no-referrer" loading="lazy" onError={(e) => (e.currentTarget.style.display = "none")} />
                ) : null}
                <span>{(c.display_name || c.username)[0]?.toUpperCase()}</span>
              </div>
              <div className="cand-body">
                <div className="cand-name">{c.display_name || c.username}</div>
                <div className="cand-user">
                  @{c.username}
                  {c.location && <span className="muted"> · {c.location}</span>}
                </div>
                {c.bio && <div className="cand-bio muted small">{c.bio}</div>}
                <div className="cand-score">
                  <div className="bar">
                    <div className="bar-fill" style={{ width: `${Math.min(100, (c.score / max) * 100)}%` }} />
                  </div>
                  <span className="small">{c.score <= 1 ? (c.score * 100).toFixed(0) : c.score.toFixed(c.score % 1 ? 1 : 0)}</span>
                </div>
                <div className="cand-sources">
                  {c.sources.map((s) => (
                    <span key={s} className="chip chip-dim">
                      {s}
                    </span>
                  ))}
                  {isHttpUrl(c.profile_url) && (
                    <a href={c.profile_url} target="_blank" rel="noreferrer noopener" className="chip chip-link">
                      profile ↗
                    </a>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
