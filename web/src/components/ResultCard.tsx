import { memo, useState } from "react";
import type { Fingerprint, Result, Verdict } from "../types";
import { confClass, favicon, fmtDate, hostOf, isHttpUrl, truncate } from "../util";
import StatusPill from "./StatusPill";

interface Props {
  r: Result;
  onFeedback: (r: Result, v: Verdict) => void;
}

function ResultCardInner({ r, onFeedback }: Props) {
  const [open, setOpen] = useState(false);
  const [imgOk, setImgOk] = useState(true);
  const ids = Object.entries(r.ids || {}).filter(([k]) => k !== "image");
  const image = r.ids?.image;
  const ev = r.evidence || {};
  const fb = r.feedback;
  return (
    <article className={`card result ${confClass(r.confidence)} ${fb === "false_positive" ? "is-fp" : ""} ${fb === "confirmed" ? "is-confirmed" : ""}`}>
      <header className="result-head">
        {imgOk ? (
          <img className="favicon" src={favicon(r.url_main || r.url)} alt="" width={20} height={20} loading="lazy" onError={() => setImgOk(false)} />
        ) : (
          <span className="favicon favicon-fallback">{r.site[0]}</span>
        )}
        <div className="result-title">
          <a href={r.url_main || r.url} target="_blank" rel="noreferrer noopener" className="site-name" title={hostOf(r.url_main || r.url)}>
            {r.site}
          </a>
          <a href={r.url} target="_blank" rel="noreferrer noopener" className="result-user" title={r.url}>
            {r.username} ↗
          </a>
        </div>
        <span className={`conf-badge ${confClass(r.confidence)}`} title="Confidence">
          {Math.round(r.confidence)}
        </span>
      </header>

      <div className="result-meta">
        <StatusPill status={r.status} />
        {r.http_status != null && <span className="chip chip-dim">HTTP {r.http_status}</span>}
        {fb && <span className={`chip chip-fb-${fb}`}>{fb === "confirmed" ? "✓ confirmed" : "✗ false positive"}</span>}
        {(r.tags || []).slice(0, 4).map((t) => (
          <span key={t} className="chip chip-tag">
            {t}
          </span>
        ))}
        {(r.tags || []).length > 4 && <span className="chip chip-dim">+{r.tags.length - 4}</span>}
      </div>

      {(ids.length > 0 || image) && (
        <div className="result-ids">
          {isHttpUrl(image) && <img className="profile-img" src={image} alt="" loading="lazy" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.display = "none")} />}
          <dl>
            {ids.slice(0, 6).map(([k, v]) => (
              <div key={k} className="kv">
                <dt title={k}>{k}</dt>
                <dd title={String(v)}>{isHttpUrl(v) ? <a href={v} target="_blank" rel="noreferrer noopener">{truncate(v, 48)}</a> : truncate(String(v), 64)}</dd>
              </div>
            ))}
            {ids.length > 6 && <div className="muted small">+{ids.length - 6} more {ids.length - 6 === 1 ? "field" : "fields"}</div>}
          </dl>
        </div>
      )}

      <footer className="result-foot">
        <button className="btn btn-ghost btn-xs" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <span className={`caret ${open ? "open" : ""}`}>▸</span> Evidence
        </button>
        <div className="fb-buttons">
          <button
            className={`btn btn-xs btn-ok ${fb === "confirmed" ? "active" : ""}`}
            title="Confirm this is the target"
            onClick={() => onFeedback(r, fb === "confirmed" ? null : "confirmed")}
          >
            ✓ Confirm
          </button>
          <button
            className={`btn btn-xs btn-bad ${fb === "false_positive" ? "active" : ""}`}
            title="Mark as false positive"
            onClick={() => onFeedback(r, fb === "false_positive" ? null : "false_positive")}
          >
            ✗ FP
          </button>
        </div>
      </footer>

      {open && (
        <div className="evidence">
          <div className="section-label">Reasons</div>
          {r.reasons?.length ? (
            <ul className="reasons">
              {r.reasons.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          ) : (
            <p className="muted small">No reasons recorded.</p>
          )}
          {(ev.target || ev.control) && (
            <>
              <div className="section-label">
                Fingerprints
                {ev.similarity != null && (
                  <span className={`sim ${ev.similarity >= 0.9 ? "bad-text" : ev.similarity >= 0.6 ? "warn-text" : "good-text"}`}>
                    similarity {(ev.similarity * (ev.similarity <= 1 ? 100 : 1)).toFixed(0)}%
                  </span>
                )}
              </div>
              <FingerprintTable target={ev.target} control={ev.control} />
            </>
          )}
          <div className="muted small">checked {fmtDate(r.checked_at)}</div>
        </div>
      )}
    </article>
  );
}

function FingerprintTable({ target, control }: { target?: Fingerprint; control?: Fingerprint }) {
  const rows: { k: string; t: string; c: string }[] = [
    { k: "status", t: String(target?.status ?? "—"), c: String(control?.status ?? "—") },
    { k: "length", t: target ? String(target.length) : "—", c: control ? String(control.length) : "—" },
    { k: "title", t: target?.title || "—", c: control?.title || "—" },
    { k: "final_url", t: target?.final_url || "—", c: control?.final_url || "—" },
  ];
  return (
    <table className="fp-table">
      <thead>
        <tr>
          <th />
          <th>target</th>
          <th>control</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.k} className={target && control && r.t !== r.c ? "diff" : ""}>
            <th>{r.k}</th>
            <td title={r.t}>{truncate(r.t, 80)}</td>
            <td title={r.c}>{truncate(r.c, 80)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const ResultCard = memo(ResultCardInner);
export default ResultCard;
