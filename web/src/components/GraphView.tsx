import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent, type WheelEvent as RWheelEvent } from "react";
import type { GraphData } from "../types";
import { truncate } from "../util";

interface SimNode {
  id: string;
  label: string;
  type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fixed?: boolean;
}

const TYPE_COLORS: Record<string, string> = {
  search: "#ff2a6d",
  query: "#ff2a6d",
  username: "#b026ff",
  candidate: "#b026ff",
  site: "#05d9e8",
  account: "#05d9e8",
  result: "#05d9e8",
  email: "#ffb86b",
  name: "#ff2a6d",
  url: "#7a7aa0",
  link: "#7a7aa0",
};
const color = (t: string) => TYPE_COLORS[t] || "#d1c4ff";

export default function GraphView({ data }: { data: GraphData }) {
  const W = 1000;
  const H = 640;
  const nodesRef = useRef<SimNode[]>([]);
  const [, setTick] = useState(0);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ id: string | null; px: number; py: number; pan: boolean }>({ id: null, px: 0, py: 0, pan: false });
  const svgRef = useRef<SVGSVGElement>(null);
  const kickRef = useRef<() => void>(() => {});

  const edges = useMemo(() => data.edges.filter((e) => data.nodes.some((n) => n.id === e.source) && data.nodes.some((n) => n.id === e.target)), [data]);

  useEffect(() => {
    const prev = new Map(nodesRef.current.map((n) => [n.id, n]));
    nodesRef.current = data.nodes.map((n, i) => {
      const p = prev.get(n.id);
      const a = (i / Math.max(1, data.nodes.length)) * Math.PI * 2;
      const r = 120 + (i % 7) * 25;
      return p ? { ...p, label: n.label, type: n.type } : { id: n.id, label: n.label, type: n.type, x: W / 2 + Math.cos(a) * r, y: H / 2 + Math.sin(a) * r, vx: 0, vy: 0 };
    });
    let alpha = 1;
    let raf = 0;
    const idx = new Map<string, SimNode>();
    const step = () => {
      const ns = nodesRef.current;
      idx.clear();
      ns.forEach((n) => idx.set(n.id, n));
      const N = ns.length;
      // repulsion
      for (let i = 0; i < N; i++) {
        const a = ns[i];
        for (let j = i + 1; j < N; j++) {
          const b = ns[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) {
            dx = Math.random() - 0.5;
            dy = Math.random() - 0.5;
            d2 = 0.5;
          }
          if (d2 > 160000) continue;
          const f = (1800 * alpha) / d2;
          a.vx += dx * f;
          a.vy += dy * f;
          b.vx -= dx * f;
          b.vy -= dy * f;
        }
      }
      // springs
      for (const e of edges) {
        const s = idx.get(e.source);
        const t = idx.get(e.target);
        if (!s || !t) continue;
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = ((d - 90) / d) * 0.06 * alpha;
        s.vx += dx * f;
        s.vy += dy * f;
        t.vx -= dx * f;
        t.vy -= dy * f;
      }
      for (const n of ns) {
        n.vx += (W / 2 - n.x) * 0.004 * alpha;
        n.vy += (H / 2 - n.y) * 0.004 * alpha;
        if (n.fixed) {
          n.vx = n.vy = 0;
          continue;
        }
        n.vx *= 0.6;
        n.vy *= 0.6;
        n.x += n.vx;
        n.y += n.vy;
      }
      alpha = Math.max(0.02, alpha * 0.985);
      setTick((t) => t + 1);
      if (alpha > 0.021 || drag.current.id) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    const kick = () => {
      alpha = Math.max(alpha, 0.3);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(step);
    };
    kickRef.current = kick;
    return () => cancelAnimationFrame(raf);
  }, [data, edges]);

  const toLocal = (cx: number, cy: number) => {
    const svg = svgRef.current!;
    const r = svg.getBoundingClientRect();
    const sx = ((cx - r.left) / r.width) * W;
    const sy = ((cy - r.top) / r.height) * H;
    return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
  };

  const onDown = (e: RPointerEvent, id: string | null) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { id, px: e.clientX, py: e.clientY, pan: id === null };
    if (id) {
      const n = nodesRef.current.find((x) => x.id === id);
      if (n) n.fixed = true;
      kickRef.current();
    }
  };
  const onMove = (e: RPointerEvent) => {
    const d = drag.current;
    if (d.id) {
      const p = toLocal(e.clientX, e.clientY);
      const n = nodesRef.current.find((x) => x.id === d.id);
      if (n) {
        n.x = p.x;
        n.y = p.y;
        setTick((t) => t + 1);
      }
    } else if (d.pan) {
      const r = svgRef.current!.getBoundingClientRect();
      const dx = ((e.clientX - d.px) / r.width) * W;
      const dy = ((e.clientY - d.py) / r.height) * H;
      d.px = e.clientX;
      d.py = e.clientY;
      setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
    }
  };
  const onUp = () => {
    const d = drag.current;
    if (d.id) {
      const n = nodesRef.current.find((x) => x.id === d.id);
      if (n) n.fixed = false;
    }
    drag.current = { id: null, px: 0, py: 0, pan: false };
  };
  const onWheel = (e: RWheelEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * W;
    const sy = ((e.clientY - r.top) / r.height) * H;
    setView((v) => {
      const k = Math.min(4, Math.max(0.2, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      return { k, x: sx - ((sx - v.x) * k) / v.k, y: sy - ((sy - v.y) * k) / v.k };
    });
  };

  if (!data.nodes.length) return <div className="card empty muted">Graph is empty.</div>;

  const ns = nodesRef.current;
  const idx = new Map(ns.map((n) => [n.id, n]));
  const deg = new Map<string, number>();
  edges.forEach((e) => {
    deg.set(e.source, (deg.get(e.source) || 0) + 1);
    deg.set(e.target, (deg.get(e.target) || 0) + 1);
  });
  const neighbors = new Set<string>();
  if (hover) {
    neighbors.add(hover);
    edges.forEach((e) => {
      if (e.source === hover) neighbors.add(e.target);
      if (e.target === hover) neighbors.add(e.source);
    });
  }
  const types = Array.from(new Set(data.nodes.map((n) => n.type)));

  return (
    <div className="card graph-wrap">
      <div className="graph-legend">
        {types.map((t) => (
          <span key={t} className="legend-item">
            <i style={{ background: color(t), boxShadow: `0 0 8px ${color(t)}` }} />
            {t}
          </span>
        ))}
        <span className="muted small">
          {data.nodes.length} nodes · {edges.length} edges · scroll to zoom, drag to pan
        </span>
        <button className="btn btn-ghost btn-xs" onClick={() => setView({ x: 0, y: 0, k: 1 })}>
          Reset view
        </button>
      </div>
      <svg
        ref={svgRef}
        className="graph"
        viewBox={`0 0 ${W} ${H}`}
        onPointerDown={(e) => onDown(e, null)}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={onUp}
        onWheel={onWheel}
      >
        <defs>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {edges.map((e, i) => {
            const s = idx.get(e.source);
            const t = idx.get(e.target);
            if (!s || !t) return null;
            const hl = hover && (e.source === hover || e.target === hover);
            return (
              <g key={i} opacity={hover && !hl ? 0.12 : 1}>
                <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} className={`g-edge ${hl ? "hl" : ""}`} />
                {hl && e.label && (
                  <text x={(s.x + t.x) / 2} y={(s.y + t.y) / 2} className="g-edge-label">
                    {e.label}
                  </text>
                )}
              </g>
            );
          })}
          {ns.map((n) => {
            const r = 5 + Math.min(10, Math.sqrt(deg.get(n.id) || 0) * 2.2);
            const dim = hover && !neighbors.has(n.id);
            return (
              <g
                key={n.id}
                transform={`translate(${n.x},${n.y})`}
                opacity={dim ? 0.15 : 1}
                onPointerDown={(e) => onDown(e, n.id)}
                onPointerEnter={() => setHover(n.id)}
                onPointerLeave={() => setHover(null)}
                className="g-node"
              >
                <circle r={r} fill={color(n.type)} filter="url(#glow)" />
                <text y={r + 11} className="g-label">
                  {truncate(n.label, 28)}
                </text>
                <title>
                  {n.type}: {n.label}
                </title>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
