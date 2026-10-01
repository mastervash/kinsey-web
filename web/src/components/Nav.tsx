import { NavLink } from "react-router-dom";
import { useStats } from "../hooks";

export default function Nav() {
  const { stats, online } = useStats();
  return (
    <header className="nav">
      <NavLink to="/" className="logo" aria-label="MAIGRET//WEB home">
        MAIGRET<span className="logo-sep">//</span>WEB
      </NavLink>
      <nav className="nav-links">
        <NavLink to="/" end>
          Search
        </NavLink>
        <NavLink to="/history">History</NavLink>
        <NavLink to="/sites">Sites</NavLink>
        <NavLink to="/settings">Settings</NavLink>
      </nav>
      <div className="nav-stats">
        {stats ? (
          <>
            <span title="Enabled / total sites">
              <b>{stats.sites_enabled}</b>/{stats.sites_total} sites
            </span>
            {stats.sites_quarantined > 0 && (
              <span title="Quarantined sites" className="warn-text">
                <b>{stats.sites_quarantined}</b> quar.
              </span>
            )}
            <span title="Searches run">
              <b>{stats.searches}</b> {stats.searches === 1 ? "search" : "searches"}
            </span>
            <span title="Claimed results">
              <b>{stats.results_claimed}</b> hits
            </span>
          </>
        ) : null}
        <span
          className={`dot ${online === null ? "dot-wait" : online ? "dot-on" : "dot-off"}`}
          title={online === null ? "Connecting" : online ? "Backend online" : "Backend unreachable"}
        />
      </div>
    </header>
  );
}
