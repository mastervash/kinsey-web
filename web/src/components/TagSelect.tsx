import { useMemo, useState } from "react";
import type { TagCount } from "../types";

interface Props {
  tags: TagCount[];
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  variant?: "include" | "exclude";
}

export default function TagSelect({ tags, value, onChange, placeholder, variant = "include" }: Props) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const opts = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return tags.filter((t) => !value.includes(t.tag) && (!ql || t.tag.toLowerCase().includes(ql))).slice(0, 60);
  }, [tags, value, q]);

  const add = (t: string) => {
    if (!value.includes(t)) onChange([...value, t]);
    setQ("");
  };

  return (
    <div className="tagselect" onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setOpen(false)}>
      <div className="tagselect-box" onClick={() => setOpen(true)}>
        {value.map((t) => (
          <span key={t} className={`chip chip-${variant}`}>
            {t}
            <button
              type="button"
              aria-label={`remove ${t}`}
              onClick={(e) => {
                e.stopPropagation();
                onChange(value.filter((x) => x !== t));
              }}
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={q}
          placeholder={value.length ? "" : placeholder || "add tag…"}
          onFocus={() => setOpen(true)}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              const v = q.trim();
              if (opts[0]) add(opts[0].tag);
              else if (v) add(v);
            } else if (e.key === "Backspace" && !q && value.length) {
              onChange(value.slice(0, -1));
            } else if (e.key === "Escape") setOpen(false);
          }}
        />
      </div>
      {open && opts.length > 0 && (
        <div className="tagselect-menu" tabIndex={-1}>
          {opts.map((t) => (
            <button type="button" key={t.tag} onMouseDown={(e) => e.preventDefault()} onClick={() => add(t.tag)}>
              <span>{t.tag}</span>
              <span className="muted">{t.count}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
