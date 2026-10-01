import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Stats, TagCount } from "./types";

let tagsCache: TagCount[] | null = null;
let tagsPromise: Promise<TagCount[]> | null = null;

export function useTags(): TagCount[] {
  const [tags, setTags] = useState<TagCount[]>(tagsCache || []);
  useEffect(() => {
    if (tagsCache) return;
    if (!tagsPromise) {
      tagsPromise = api
        .tags()
        .then((t) => {
          const arr = Array.isArray(t) ? [...t].sort((a, b) => b.count - a.count) : [];
          tagsCache = arr;
          return arr;
        })
        .catch(() => {
          tagsPromise = null;
          return [] as TagCount[];
        });
    }
    let alive = true;
    tagsPromise.then((t) => alive && setTags(t));
    return () => {
      alive = false;
    };
  }, []);
  return tags;
}

export function useStats(intervalMs = 30000) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const load = useCallback(() => {
    api
      .stats()
      .then((s) => {
        setStats(s);
        setOnline(true);
      })
      .catch((e) => {
        setOnline(e?.status === 401 ? true : false);
      });
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, intervalMs);
    return () => clearInterval(t);
  }, [load, intervalMs]);
  return { stats, online, reload: load };
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useLatest<T>(v: T) {
  const r = useRef(v);
  r.current = v;
  return r;
}
