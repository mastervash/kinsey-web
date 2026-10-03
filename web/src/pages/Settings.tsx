import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, getToken, setToken } from "../api";
import OptionsPanel from "../components/OptionsPanel";
import { useTags } from "../hooks";
import { useToast } from "../toast";
import { DEFAULT_OPTIONS, type Health, type Settings } from "../types";

export default function SettingsPage() {
  const toast = useToast();
  const tags = useTags();
  const [params, setParams] = useSearchParams();
  const authRequired = params.get("auth") === "required";
  const [token, setTok] = useState(getToken());
  const [showTok, setShowTok] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [clearing, setClearing] = useState(false);

  const load = () => {
    setLoadErr(null);
    api
      .getSettings()
      .then((s) =>
        setSettings({
          defaults: { ...DEFAULT_OPTIONS, ...(s?.defaults || {}) },
          proxy: s?.proxy ?? "",
          tor_proxy: s?.tor_proxy ?? "",
        }),
      )
      .catch((e) => setLoadErr(e.message));
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
  };
  useEffect(load, []);

  const saveToken = () => {
    setToken(token.trim());
    toast.push(token.trim() ? "Token saved" : "Token cleared", "ok");
    if (authRequired) setParams({});
    load();
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const body: Settings = {
        ...settings,
        proxy: settings.proxy?.trim() || null,
        tor_proxy: settings.tor_proxy?.trim() || null,
      };
      const s = await api.putSettings(body);
      if (s) setSettings({ defaults: { ...DEFAULT_OPTIONS, ...s.defaults }, proxy: s.proxy ?? "", tor_proxy: s.tor_proxy ?? "" });
      toast.push("Settings saved", "ok");
    } catch (e) {
      toast.error(e);
    } finally {
      setSaving(false);
    }
  };

  const clearHistory = async () => {
    if (!confirm("Delete ALL searches and their results? Running searches are stopped. This cannot be undone.")) return;
    setClearing(true);
    try {
      const r = await api.clearSearches();
      toast.push(`Cleared ${r.deleted} ${r.deleted === 1 ? "search" : "searches"}`, "ok");
    } catch (e) {
      toast.error(e);
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="page page-narrow">
      <div className="page-head">
        <h2>Settings</h2>
        {health && (
          <span className="muted small">
            backend v{health.version} · {health.sites_enabled} sites enabled
          </span>
        )}
      </div>

      {authRequired && <div className="banner banner-bad">The backend requires an API token. Enter it below.</div>}

      <section className="card">
        <div className="card-head">
          <h3>API token</h3>
          <span className="muted small">stored in this browser only (localStorage)</span>
        </div>
        <div className="row gap">
          <input
            className="grow"
            type={showTok ? "text" : "password"}
            autoComplete="off"
            placeholder="KW_TOKEN value"
            value={token}
            onChange={(e) => setTok(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveToken()}
          />
          <button className="btn btn-ghost btn-sm" onClick={() => setShowTok((s) => !s)}>
            {showTok ? "Hide" : "Show"}
          </button>
          <button className="btn btn-primary btn-sm" onClick={saveToken}>
            Save token
          </button>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h3>Search defaults</h3>
        </div>
        {loadErr ? (
          <div className="empty">
            <p className="bad-text">{loadErr}</p>
            <button className="btn btn-ghost btn-sm" onClick={load}>
              Retry
            </button>
          </div>
        ) : !settings ? (
          <div className="muted">Loading…</div>
        ) : (
          <>
            <OptionsPanel value={settings.defaults} onChange={(d) => setSettings({ ...settings, defaults: d })} tags={tags} />
            <div className="section-label">Network</div>
            <div className="options-grid">
              <label className="field field-wide">
                <span>HTTP proxy</span>
                <input
                  value={settings.proxy ?? ""}
                  placeholder="http://user:pass@host:port"
                  onChange={(e) => setSettings({ ...settings, proxy: e.target.value })}
                />
              </label>
              <label className="field field-wide">
                <span>Tor proxy</span>
                <input
                  value={settings.tor_proxy ?? ""}
                  placeholder="socks5://127.0.0.1:9050"
                  onChange={(e) => setSettings({ ...settings, tor_proxy: e.target.value })}
                />
              </label>
            </div>
            <div className="row-end gap">
              <button className="btn btn-ghost btn-sm" onClick={() => setSettings({ ...settings, defaults: DEFAULT_OPTIONS })}>
                Reset defaults
              </button>
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? "Saving…" : "Save settings"}
              </button>
            </div>
          </>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h3>Search history</h3>
          <span className="muted small">site reliability feedback is kept</span>
        </div>
        <div className="row-end gap">
          <button className="btn btn-danger" onClick={clearHistory} disabled={clearing}>
            {clearing ? "Clearing…" : "Clear search history"}
          </button>
        </div>
      </section>
    </div>
  );
}
