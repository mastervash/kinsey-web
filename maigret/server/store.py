"""SQLite persistence for searches, results, site feedback and settings."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  query TEXT NOT NULL,
  context TEXT NOT NULL DEFAULT '{}',
  options TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  counts TEXT NOT NULL DEFAULT '{}',
  candidates TEXT NOT NULL DEFAULT '[]',
  error TEXT
);
CREATE TABLE IF NOT EXISTS results (
  id TEXT PRIMARY KEY,
  search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
  site TEXT NOT NULL,
  username TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence INTEGER,
  data TEXT NOT NULL,
  feedback TEXT
);
CREATE INDEX IF NOT EXISTS results_search ON results(search_id);
CREATE TABLE IF NOT EXISTS site_feedback (
  site TEXT PRIMARY KEY,
  fp_reports INTEGER NOT NULL DEFAULT 0,
  confirmations INTEGER NOT NULL DEFAULT 0,
  hits INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        # A crash mid-search leaves rows "running" forever.
        self.conn.execute("UPDATE searches SET status='stopped' WHERE status IN ('running','queued')")

    def _x(self, sql: str, args=()):
        with self._lock:
            return self.conn.execute(sql, args)

    # searches -------------------------------------------------------------
    @staticmethod
    def _search_row(r: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": r["id"], "kind": r["kind"], "query": r["query"],
            "context": json.loads(r["context"]), "options": json.loads(r["options"]),
            "status": r["status"], "created_at": r["created_at"], "finished_at": r["finished_at"],
            "counts": json.loads(r["counts"]), "candidates": json.loads(r["candidates"]),
            "error": r["error"],
        }

    def create_search(self, s: Dict[str, Any]):
        self._x(
            "INSERT INTO searches(id,kind,query,context,options,status,created_at,counts,candidates) VALUES(?,?,?,?,?,?,?,?,?)",
            (s["id"], s["kind"], s["query"], json.dumps(s["context"]), json.dumps(s["options"]),
             s["status"], s["created_at"], json.dumps(s["counts"]), json.dumps(s.get("candidates", []))),
        )

    def update_search(self, sid: str, **f):
        cols, vals = [], []
        for k, v in f.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v) if k in ("counts", "candidates", "context", "options") else v)
        if cols:
            self._x(f"UPDATE searches SET {','.join(cols)} WHERE id=?", (*vals, sid))

    def get_search(self, sid: str) -> Optional[Dict[str, Any]]:
        r = self._x("SELECT * FROM searches WHERE id=?", (sid,)).fetchone()
        return self._search_row(r) if r else None

    def list_searches(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [self._search_row(r) for r in self._x(
            "SELECT * FROM searches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]

    def delete_search(self, sid: str):
        self._x("DELETE FROM results WHERE search_id=?", (sid,))
        self._x("DELETE FROM searches WHERE id=?", (sid,))

    def clear_searches(self) -> int:
        """Delete every search and its results. Site feedback (reliability) is kept."""
        n = self._x("SELECT COUNT(*) FROM searches").fetchone()[0]
        self._x("DELETE FROM results")
        self._x("DELETE FROM searches")
        return n

    # results --------------------------------------------------------------
    def add_result(self, r: Dict[str, Any]):
        self._x(
            "INSERT OR REPLACE INTO results(id,search_id,site,username,status,confidence,data,feedback) VALUES(?,?,?,?,?,?,?,?)",
            (r["id"], r["search_id"], r["site"], r["username"], r["status"], r.get("confidence"),
             json.dumps(r), r.get("feedback")),
        )
        if r["status"] == "claimed":
            self._x("INSERT INTO site_feedback(site,hits) VALUES(?,1) ON CONFLICT(site) DO UPDATE SET hits=hits+1", (r["site"],))

    def results_for(self, sid: str) -> List[Dict[str, Any]]:
        out = []
        for row in self._x("SELECT data, feedback FROM results WHERE search_id=? ORDER BY confidence DESC", (sid,)):
            d = json.loads(row["data"])
            d["feedback"] = row["feedback"]
            out.append(d)
        return out

    def get_result(self, rid: str) -> Optional[Dict[str, Any]]:
        row = self._x("SELECT data, feedback FROM results WHERE id=?", (rid,)).fetchone()
        if not row:
            return None
        d = json.loads(row["data"])
        d["feedback"] = row["feedback"]
        return d

    def set_feedback(self, rid: str, verdict: Optional[str]) -> bool:
        r = self.get_result(rid)
        if not r:
            return False
        prev = r.get("feedback")
        if prev == verdict:
            return True
        col = {"false_positive": "fp_reports", "confirmed": "confirmations"}
        with self._lock:
            self._x("INSERT OR IGNORE INTO site_feedback(site) VALUES(?)", (r["site"],))
            if prev in col:
                self._x(f"UPDATE site_feedback SET {col[prev]}=MAX(0,{col[prev]}-1) WHERE site=?", (r["site"],))
            if verdict in col:
                self._x(f"UPDATE site_feedback SET {col[verdict]}={col[verdict]}+1 WHERE site=?", (r["site"],))
            self._x("UPDATE results SET feedback=? WHERE id=?", (verdict, rid))
        return True

    # site stats -----------------------------------------------------------
    def site_stats(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for r in self._x("SELECT * FROM site_feedback"):
            fp, ok = r["fp_reports"], r["confirmations"]
            rel = (ok + 1) / (ok + fp + 2) if (ok + fp) else None  # Laplace-smoothed
            out[r["site"]] = {"fp_reports": fp, "confirmations": ok, "hits": r["hits"], "reliability": rel}
        return out

    # settings / stats -----------------------------------------------------
    def kv_get(self, k: str, default=None):
        r = self._x("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v):
        self._x("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))

    def totals(self) -> Dict[str, int]:
        q = lambda sql: self._x(sql).fetchone()[0] or 0  # noqa: E731
        return {
            "searches": q("SELECT COUNT(*) FROM searches"),
            "results_claimed": q("SELECT COUNT(*) FROM results WHERE status='claimed'"),
            "feedback_fp": q("SELECT SUM(fp_reports) FROM site_feedback"),
            "feedback_confirmed": q("SELECT SUM(confirmations) FROM site_feedback"),
        }
