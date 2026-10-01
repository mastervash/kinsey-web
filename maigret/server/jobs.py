"""Search orchestration: runs the maigret engine and fans results out to SSE."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from ..checking import maigret as engine_search
from ..errors import CheckError
from ..people import find_candidates
from ..result import MaigretCheckStatus
from ..sites import MaigretDatabase, MaigretSite
from .store import Store, now

log = logging.getLogger("maigret.server")

NSFW_TAGS = {"porn", "erotic", "nsfw", "webcam"}
BLOCK_ERRORS = {"Captcha", "Bot protection", "Access denied", "Blocked", "Request blocked", "Login required", "Rate limited"}

DEFAULT_OPTIONS: Dict[str, Any] = {
    "top_sites": 500,
    "tags": [],
    "exclude_tags": [],
    "sites": [],
    "timeout": 10,
    "min_confidence": 60,
    "control_probe": True,
    "include_nsfw": False,
    "max_candidates": 8,
    "recursive": False,
}


def normalize_options(opts: Optional[dict], defaults: Optional[dict] = None) -> Dict[str, Any]:
    out = dict(DEFAULT_OPTIONS)
    out.update(defaults or {})
    for k, v in (opts or {}).items():
        if k in DEFAULT_OPTIONS and v is not None:
            out[k] = v
    out["top_sites"] = max(0, int(out["top_sites"]))
    out["timeout"] = max(2, min(60, int(out["timeout"])))
    out["min_confidence"] = max(0, min(100, int(out["min_confidence"])))
    out["max_candidates"] = max(1, min(25, int(out["max_candidates"])))
    return out


def select_sites(db: MaigretDatabase, o: Dict[str, Any]) -> Dict[str, MaigretSite]:
    excluded = list(o["exclude_tags"])
    if not o["include_nsfw"]:
        excluded += [t for t in NSFW_TAGS if t not in o["tags"]]
    top = o["top_sites"] or 10**9
    sites = db.ranked_sites_dict(
        top=top if not o["sites"] else 10**9,
        tags=o["tags"],
        excluded_tags=excluded,
        names=o["sites"],
        disabled=False,
    )
    return {
        k: s for k, s in sites.items()
        if not getattr(s, "quarantined", False) and (o["include_nsfw"] or not getattr(s, "nsfw", False))
    }


def map_status(result, min_conf: int) -> str:
    st = result.status
    if st == MaigretCheckStatus.CLAIMED:
        conf = getattr(result, "confidence", None)
        if conf is None or conf >= min_conf:
            return "claimed"
        return "uncertain"
    if st == MaigretCheckStatus.AVAILABLE:
        return "available"
    if st == MaigretCheckStatus.ILLEGAL:
        return "illegal"
    err = getattr(result, "error", None)
    if isinstance(err, CheckError) and err.type in BLOCK_ERRORS:
        return "blocked"
    return "unknown"


class _Notify:
    """query_notify shim; one per engine run (one username)."""

    def __init__(self, job: "Job", username: str, sites: Dict[str, MaigretSite]):
        self.job = job
        self.username = username
        self.by_pretty = {s.pretty_name: s for s in sites.values()}

    def update(self, result, is_similar=False):
        self.job.on_result(self.username, result, self.by_pretty.get(result.site_name))

    def __getattr__(self, name):
        return lambda *a, **k: None


class Job:
    def __init__(self, manager: "SearchManager", search: Dict[str, Any]):
        self.m = manager
        self.search = search
        self.id = search["id"]
        self.subscribers: List[asyncio.Queue] = []
        self.task: Optional[asyncio.Task] = None
        self.counts = search["counts"]

    # pub/sub ---------------------------------------------------------------
    def publish(self, event: str, data: Any):
        for q in list(self.subscribers):
            q.put_nowait((event, data))

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.subscribers:
            self.subscribers.remove(q)

    # results ---------------------------------------------------------------
    def on_result(self, username: str, result, site: Optional[MaigretSite]):
        self.counts["checked"] += 1
        o = self.search["options"]
        status = map_status(result, o["min_confidence"])
        if status in ("claimed", "uncertain", "blocked", "unknown"):
            if status in ("claimed", "uncertain"):
                self.counts[status] += 1
            ids = {
                k: (v if isinstance(v, (str, int, float)) else str(v))
                for k, v in (getattr(result, "ids_data", None) or {}).items()
                if not str(k).startswith("_")
            }
            err = getattr(result, "error", None)
            reasons = list(getattr(result, "reasons", None) or [])
            if err and status in ("blocked", "unknown"):
                reasons.append(f"{err.type}: {err.desc}" if getattr(err, "desc", None) else str(err.type))
            site_name = site.name if site else result.site_name
            r = {
                "id": f"{self.id}:{site_name}:{username}",
                "search_id": self.id,
                "site": site_name,
                "url_main": site.url_main if site else "",
                "url": result.site_url_user or "",
                "username": username,
                "status": status,
                "confidence": getattr(result, "confidence", None) if status in ("claimed", "uncertain") else 0,
                "reasons": reasons,
                "http_status": None,
                "tags": list(site.tags) if site else list(result.tags or []),
                "ids": ids,
                "evidence": getattr(result, "evidence", None) or {},
                "feedback": None,
                "checked_at": now(),
            }
            if r["confidence"] is None:
                r["confidence"] = 50
            ev = r["evidence"].get("target") if r["evidence"] else None
            if ev:
                r["http_status"] = ev.get("status")
            self.m.store.add_result(r)
            self.publish("result", r)
        if self.counts["checked"] % 5 == 0 or self.counts["checked"] >= self.counts["total"]:
            self.publish("progress", {"checked": self.counts["checked"], "total": self.counts["total"]})

    # run -------------------------------------------------------------------
    async def run(self):
        s = self.search
        o = s["options"]
        store = self.m.store
        try:
            store.update_search(self.id, status="running")
            s["status"] = "running"
            sites = select_sites(self.m.db, o)
            if s["kind"] == "username":
                usernames = [s["query"].strip().lstrip("@")]
            else:
                cands = await find_candidates(s["kind"], s["query"], s["context"], o["max_candidates"], self.m.settings().get("proxy") or None)
                s["candidates"] = cands
                store.update_search(self.id, candidates=cands)
                self.publish("candidates", {"candidates": cands})
                usernames = [c["username"] for c in cands[: o["max_candidates"]]]
            self.counts["total"] = len(sites) * len(usernames)
            store.update_search(self.id, counts=self.counts)
            self.publish("start", {"total": self.counts["total"]})

            seen = set()
            queue = list(usernames)
            while queue:
                u = queue.pop(0)
                if u.lower() in seen:
                    continue
                seen.add(u.lower())
                res = await self._scan(u, sites)
                if o["recursive"] and len(seen) < 6:
                    extra = []
                    for v in res.values():
                        st = v.get("status")
                        if st and st.status == MaigretCheckStatus.CLAIMED and (getattr(st, "confidence", 0) or 0) >= o["min_confidence"]:
                            extra += [n for n, t in (v.get("ids_usernames") or {}).items() if t == "username"]
                    extra = [e for e in dict.fromkeys(extra) if e.lower() not in seen][: 6 - len(seen)]
                    if extra:
                        queue += extra
                        self.counts["total"] += len(sites) * len(extra)
                        self.publish("start", {"total": self.counts["total"]})
                store.update_search(self.id, counts=self.counts)
            s["status"] = "done"
            self.counts["checked"] = self.counts["total"]  # sites skipped by engine (filtered/errored) still count as done
        except asyncio.CancelledError:
            s["status"] = "stopped"
        except Exception as e:  # surface, never crash the server
            log.exception("search %s failed", self.id)
            s["status"] = "error"
            store.update_search(self.id, error=str(e))
            self.publish("error", {"message": str(e)})
        finally:
            store.update_search(self.id, status=s["status"], finished_at=now(), counts=self.counts)
            self.publish("done", {"status": s["status"], "counts": self.counts})
            self.m.jobs.pop(self.id, None)

    async def _scan(self, username: str, sites: Dict[str, MaigretSite]):
        o = self.search["options"]
        st = self.m.settings()
        return await engine_search(
            username=username,
            site_dict=sites,
            logger=self.m.engine_log,
            query_notify=_Notify(self, username, sites),
            proxy=st.get("proxy") or None,
            tor_proxy=st.get("tor_proxy") or None,
            timeout=o["timeout"],
            is_parsing_enabled=True,
            no_progressbar=True,
            max_connections=st.get("max_connections", 50),
            retries=0,
            control_probe=o["control_probe"],
            site_stats=self.m.store.site_stats(),
        )


class SearchManager:
    def __init__(self, store: Store, db: MaigretDatabase):
        self.store = store
        self.db = db
        self.jobs: Dict[str, Job] = {}
        self.engine_log = logging.getLogger("maigret.engine")
        self.engine_log.setLevel(logging.ERROR)
        self._sem = asyncio.Semaphore(2)

    def settings(self) -> Dict[str, Any]:
        return self.store.kv_get("settings", {}) or {}

    def start(self, kind: str, query: str, context: dict, options: dict) -> Dict[str, Any]:
        o = normalize_options(options, self.settings().get("defaults"))
        search = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "query": query.strip(),
            "context": context or {},
            "options": o,
            "status": "queued",
            "created_at": now(),
            "finished_at": None,
            "counts": {"total": 0, "checked": 0, "claimed": 0, "uncertain": 0},
            "candidates": [],
        }
        self.store.create_search(search)
        job = Job(self, search)
        self.jobs[search["id"]] = job

        async def guarded():
            async with self._sem:
                await job.run()

        job.task = asyncio.get_running_loop().create_task(guarded())
        return search

    def stop(self, sid: str) -> bool:
        job = self.jobs.get(sid)
        if not job or not job.task:
            return False
        job.task.cancel()
        return True
