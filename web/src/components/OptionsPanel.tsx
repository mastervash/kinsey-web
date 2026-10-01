import type { Kind, SearchOptions, TagCount } from "../types";
import TagSelect from "./TagSelect";

interface Props {
  value: SearchOptions;
  onChange: (v: SearchOptions) => void;
  tags: TagCount[];
  kind?: Kind;
}

export default function OptionsPanel({ value, onChange, tags, kind }: Props) {
  const set = <K extends keyof SearchOptions>(k: K, v: SearchOptions[K]) => onChange({ ...value, [k]: v });
  const num = (s: string, fallback: number) => {
    if (s.trim() === "") return fallback;
    const n = Number(s);
    return isFinite(n) ? n : fallback;
  };
  return (
    <div className="options-grid">
      <label className="field">
        <span>Top sites <small className="muted">(0 = all enabled)</small></span>
        <input
          type="number"
          min={0}
          value={value.top_sites}
          onChange={(e) => set("top_sites", Math.max(0, Math.floor(num(e.target.value, 0))))}
        />
      </label>
      <label className="field">
        <span>Timeout <small className="muted">(s)</small></span>
        <input
          type="number"
          min={1}
          max={120}
          value={value.timeout}
          onChange={(e) => set("timeout", Math.max(1, num(e.target.value, 10)))}
        />
      </label>
      {(kind === undefined || kind !== "username") && (
        <label className="field">
          <span>Max candidates <small className="muted">(name/email)</small></span>
          <input
            type="number"
            min={1}
            max={100}
            value={value.max_candidates}
            onChange={(e) => set("max_candidates", Math.max(1, Math.floor(num(e.target.value, 8))))}
          />
        </label>
      )}
      <label className="field field-wide">
        <span>
          Min confidence <b className="accent">{value.min_confidence}</b>
        </span>
        <input
          type="range"
          min={0}
          max={100}
          value={value.min_confidence}
          onChange={(e) => set("min_confidence", Number(e.target.value))}
        />
      </label>
      <div className="field field-wide">
        <span>Tags</span>
        <TagSelect tags={tags} value={value.tags} onChange={(v) => set("tags", v)} placeholder="limit to tags…" />
      </div>
      <div className="field field-wide">
        <span>Exclude tags</span>
        <TagSelect
          tags={tags}
          value={value.exclude_tags}
          onChange={(v) => set("exclude_tags", v)}
          placeholder="exclude tags…"
          variant="exclude"
        />
      </div>
      <label className="field field-wide">
        <span>Explicit sites <small className="muted">(comma separated, overrides top/tags)</small></span>
        <input
          value={value.sites.join(", ")}
          placeholder="GitHub, Reddit…"
          onChange={(e) =>
            set(
              "sites",
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
        />
      </label>
      <div className="toggles field-wide">
        <Toggle label="Control probe" hint="random-username baseline" checked={value.control_probe} onChange={(v) => set("control_probe", v)} />
        <Toggle label="Include NSFW" checked={value.include_nsfw} onChange={(v) => set("include_nsfw", v)} />
        <Toggle label="Recursive" hint="follow extracted usernames" checked={value.recursive} onChange={(v) => set("recursive", v)} />
      </div>
    </div>
  );
}

export function Toggle({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`toggle ${disabled ? "is-disabled" : ""}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track">
        <span className="toggle-thumb" />
      </span>
      <span>
        {label}
        {hint && <small className="muted"> — {hint}</small>}
      </span>
    </label>
  );
}
