"""kinsey-web FastAPI app. Contract: docs/API.md."""

from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..__version__ import __version__
from ..sites import MaigretDatabase
from .jobs import DEFAULT_OPTIONS, SearchManager, normalize_options, webgate_config, webgate_settings
from .store import Store


def _env(name: str, default: str) -> str:
    """KW_* env var, falling back to the legacy MW_* name."""
    return os.environ.get(f"KW_{name}") or os.environ.get(f"MW_{name}") or default


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_FILE = _env("SITES_DB", os.path.join(ROOT, "maigret", "resources", "data.json"))
DATA_DIR = _env("DATA_DIR", os.path.join(ROOT, "var"))
DIST = _env("DIST", os.path.join(ROOT, "web", "dist"))
TOKEN = _env("TOKEN", "")

log = logging.getLogger("kinsey.server")


class State:
    db: MaigretDatabase
    store: Store
    mgr: SearchManager
    db_lock = asyncio.Lock()


S = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.db = MaigretDatabase().load_from_path(DB_FILE)
    S.store = Store(os.path.join(DATA_DIR, "kinsey-web.sqlite3"))
    S.mgr = SearchManager(S.store, S.db)
    yield


app = FastAPI(title="kinsey-web", version=__version__, lifespan=lifespan)


def auth(request: Request):
    if not TOKEN:
        return
    h = request.headers.get("authorization", "")
    tok = h[7:] if h.lower().startswith("bearer ") else request.query_params.get("token", "")
    if tok != TOKEN:
        raise HTTPException(401, "unauthorized")


A = [Depends(auth)]


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class SearchIn(BaseModel):
    kind: Literal["username", "name", "email"]
    query: str = Field(min_length=1, max_length=200)
    context: Dict[str, Any] = {}
    options: Dict[str, Any] = {}


class FeedbackIn(BaseModel):
    result_id: str
    verdict: Optional[Literal["confirmed", "false_positive"]] = None


class SiteTestIn(BaseModel):
    username: Optional[str] = None


# --------------------------------------------------------------------------- #
# searches
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "sites_enabled": _enabled_count()}


def _enabled_count():
    return sum(1 for s in S.db.sites if not s.disabled and not getattr(s, "quarantined", False))


@app.post("/api/searches", dependencies=A)
async def create_search(body: SearchIn):
    q = body.query.strip()
    if body.kind == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", q):
        raise HTTPException(422, "invalid email")
    if body.kind == "username" and (len(q) > 100 or re.search(r"\s", q)):
        raise HTTPException(422, "username must not contain whitespace")
    return S.mgr.start(body.kind, q, body.context, body.options)


@app.get("/api/searches", dependencies=A)
def list_searches(limit: int = 50):
    return S.store.list_searches(max(1, min(500, limit)))


def _search_or_404(sid: str):
    s = S.store.get_search(sid)
    if not s:
        raise HTTPException(404, "search not found")
    job = S.mgr.jobs.get(sid)
    if job:
        s["counts"] = job.counts
        s["status"] = job.search["status"]
        s["candidates"] = job.search.get("candidates", s["candidates"])
    return s


@app.get("/api/searches/{sid}", dependencies=A)
def get_search(sid: str):
    s = _search_or_404(sid)
    return {"search": s, "results": S.store.results_for(sid)}


@app.delete("/api/searches", dependencies=A)
def clear_searches():
    S.mgr.stop_all()
    return {"ok": True, "deleted": S.store.clear_searches()}


@app.delete("/api/searches/{sid}", dependencies=A)
def delete_search(sid: str):
    S.mgr.stop(sid)
    S.store.delete_search(sid)
    return {"ok": True}


@app.post("/api/searches/{sid}/stop", dependencies=A)
def stop_search(sid: str):
    _search_or_404(sid)
    if not S.mgr.stop(sid):
        S.store.update_search(sid, status="stopped")
    return {"ok": True}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.get("/api/searches/{sid}/events", dependencies=A)
async def search_events(sid: str, request: Request):
    s = _search_or_404(sid)
    job = S.mgr.jobs.get(sid)
    q = job.subscribe() if job else None

    async def gen():
        try:
            yield _sse("start", {"total": s["counts"].get("total", 0)})
            if s.get("candidates"):
                yield _sse("candidates", {"candidates": s["candidates"]})
            for r in S.store.results_for(sid):
                yield _sse("result", r)
            yield _sse("progress", {"checked": s["counts"].get("checked", 0), "total": s["counts"].get("total", 0)})
            if q is None:
                yield _sse("done", {"status": s["status"], "counts": s["counts"]})
                return
            seen = {r["id"] for r in S.store.results_for(sid)}
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev, data = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if ev == "result":
                    if data["id"] in seen:
                        continue
                    seen.add(data["id"])
                yield _sse(ev, data)
                if ev == "done":
                    return
        finally:
            if job and q is not None:
                job.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- #
# export / graph
# --------------------------------------------------------------------------- #
EXPORT_FIELDS = ["site", "username", "url", "status", "confidence", "tags", "feedback", "reasons", "ids"]


@app.get("/api/searches/{sid}/export", dependencies=A)
def export(sid: str, format: Literal["json", "csv", "html"] = "json", min_confidence: int = 0):
    s = _search_or_404(sid)
    rows = [r for r in S.store.results_for(sid)
            if r["status"] in ("claimed", "uncertain") and (r.get("confidence") or 0) >= min_confidence]
    base = re.sub(r"[^\w.-]", "_", f"kinsey_{s['kind']}_{s['query']}")[:80]
    if format == "json":
        body = json.dumps({"search": s, "results": rows}, indent=2, default=str)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{base}.json"'})
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(EXPORT_FIELDS)
        for r in rows:
            w.writerow([r["site"], r["username"], r["url"], r["status"], r.get("confidence"),
                        ";".join(r.get("tags", [])), r.get("feedback") or "", " | ".join(r.get("reasons", [])),
                        json.dumps(r.get("ids", {}), ensure_ascii=False)])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{base}.csv"'})
    e = html.escape
    trs = "".join(
        f"<tr><td>{e(r['site'])}</td><td>{e(r['username'])}</td><td><a href='{e(r['url'])}'>{e(r['url'])}</a></td>"
        f"<td>{e(r['status'])}</td><td>{r.get('confidence')}</td>"
        f"<td>{'<br>'.join(e(f'{k}: {v}') for k, v in (r.get('ids') or {}).items())}</td></tr>"
        for r in rows
    )
    doc = f"""<!doctype html><meta charset=utf-8><title>{e(s['query'])}</title>
<style>body{{background:#05050a;color:#e8e6f0;font:14px system-ui;margin:2rem}}a{{color:#ff2a6d}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #2a1f3d;padding:.4rem;vertical-align:top}}
th{{background:#140a22;color:#b026ff}}</style>
<h1>{e(s['kind'])}: {e(s['query'])}</h1><p>{e(s['created_at'])} &middot; {len(rows)} results</p>
<table><tr><th>site</th><th>username</th><th>url</th><th>status</th><th>conf</th><th>fields</th></tr>{trs}</table>"""
    return Response(doc, media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{base}.html"'})


@app.get("/api/searches/{sid}/graph", dependencies=A)
def graph(sid: str, min_confidence: int = 0):
    s = _search_or_404(sid)
    nodes: Dict[str, Dict[str, str]] = {}
    edges: List[Dict[str, str]] = []

    def node(i, label, t):
        nodes.setdefault(i, {"id": i, "label": label, "type": t})

    root = f"q:{s['query']}"
    node(root, s["query"], s["kind"])
    for c in s.get("candidates") or []:
        cid = f"u:{c['username'].lower()}"
        node(cid, c["username"], "username")
        edges.append({"source": root, "target": cid, "label": "candidate"})
    for r in S.store.results_for(sid):
        if r["status"] != "claimed" or r.get("feedback") == "false_positive":
            continue
        uid = f"u:{r['username'].lower()}"
        node(uid, r["username"], "username")
        if s["kind"] == "username" and uid != f"u:{s['query'].lower()}":
            edges.append({"source": root, "target": uid, "label": "recursive"})
        acc = f"a:{r['id']}"
        node(acc, r["site"], "account")
        edges.append({"source": uid, "target": acc, "label": "has account"})
        for k, v in (r.get("ids") or {}).items():
            v = str(v)
            if len(v) > 120:
                continue
            if k in ("fullname", "name", "full_name"):
                nid = f"n:{v.lower()}"
                node(nid, v, "name")
            elif "email" in k:
                nid = f"e:{v.lower()}"
                node(nid, v, "email")
            elif k in ("location", "country", "city"):
                nid = f"l:{v.lower()}"
                node(nid, v, "location")
            elif v.startswith("http") and k in ("website", "links", "link"):
                host = urlparse(v).netloc or v
                nid = f"w:{v.lower()}"
                node(nid, host, "link")
            elif "username" in k:
                nid = f"u:{v.lower()}"
                node(nid, v, "username")
            else:
                continue
            edges.append({"source": acc, "target": nid, "label": k})
    if s["kind"] == "username":
        nodes.pop(f"u:{s['query'].lower()}", None)
        for ed in edges:
            for side in ("source", "target"):
                if ed[side] == f"u:{s['query'].lower()}":
                    ed[side] = root
    return {"nodes": list(nodes.values()), "edges": [e for e in edges if e["source"] != e["target"]]}


@app.post("/api/results/feedback", dependencies=A)
def feedback(body: FeedbackIn):
    if not S.store.set_feedback(body.result_id, body.verdict):
        raise HTTPException(404, "result not found")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# sites
# --------------------------------------------------------------------------- #
def _site_row(s, stats) -> Dict[str, Any]:
    st = stats.get(s.name, {})
    return {
        "name": s.name, "url_main": s.url_main, "url": s.url, "tags": list(s.tags),
        "check_type": s.check_type, "disabled": bool(s.disabled),
        "quarantined": bool(getattr(s, "quarantined", False)),
        "quarantine_reason": getattr(s, "quarantine_reason", None),
        "rank": s.alexa_rank if s.alexa_rank and s.alexa_rank < 10**9 else None,
        "tranco_rank": getattr(s, "tranco_rank", None),
        "reliability": st.get("reliability"), "fp_reports": st.get("fp_reports", 0),
        "confirmations": st.get("confirmations", 0),
        "last_verified": getattr(s, "last_verified", None),
        "source": getattr(s, "origin", None) or "maigret",
    }


def _site(name: str):
    s = S.db.sites_dict.get(name)
    if not s:
        raise HTTPException(404, "site not found")
    return s


@app.get("/api/sites", dependencies=A)
def sites(q: str = "", tag: str = "", state: str = "", sort: str = "rank", offset: int = 0, limit: int = 100):
    stats = S.store.site_stats()
    items = S.db.sites
    if q:
        ql = q.lower()
        items = [s for s in items if ql in s.name.lower() or ql in (s.url_main or "").lower()]
    if tag:
        items = [s for s in items if tag in s.tags]
    if state == "enabled":
        items = [s for s in items if not s.disabled and not getattr(s, "quarantined", False)]
    elif state == "disabled":
        items = [s for s in items if s.disabled]
    elif state == "quarantined":
        items = [s for s in items if getattr(s, "quarantined", False)]
    if sort == "name":
        items = sorted(items, key=lambda s: s.name.lower())
    elif sort == "reliability":
        items = sorted(items, key=lambda s: stats.get(s.name, {}).get("reliability") or 0.5)
    else:
        items = sorted(items, key=lambda s: s.alexa_rank or 10**12)
    limit = max(1, min(500, limit))
    return {"total": len(items), "items": [_site_row(s, stats) for s in items[offset: offset + limit]]}


@app.get("/api/sites/{name}", dependencies=A)
def site(name: str):
    s = _site(name)
    row = _site_row(s, S.store.site_stats())
    row["raw"] = s.json
    return row


EDITABLE = {"disabled", "tags", "presenseStrs", "presenceStrs", "absenceStrs", "checkType", "url", "quarantined"}


@app.patch("/api/sites/{name}", dependencies=A)
async def patch_site(name: str, body: Dict[str, Any]):
    s = _site(name)
    bad = set(body) - EDITABLE
    if bad:
        raise HTTPException(422, f"not editable: {sorted(bad)}")
    upd = dict(body)
    if "presenceStrs" in upd:
        upd["presenseStrs"] = upd.pop("presenceStrs")
    if "checkType" in upd and upd["checkType"] not in ("message", "status_code", "response_url"):
        raise HTTPException(422, "bad checkType")
    from ..utils import CaseConverter
    async with S.db_lock:
        for k, v in upd.items():
            setattr(s, CaseConverter.camel_to_snake(k), v)
        if not upd.get("quarantined", True):
            s.quarantine_reason = None
        s.update_detectors()
        await asyncio.to_thread(S.db.save_to_file, DB_FILE)
    return _site_row(s, S.store.site_stats())


@app.post("/api/sites/{name}/test", dependencies=A)
async def test_site(name: str, body: SiteTestIn):
    from ..checking import maigret as engine_search
    from ..scoring import control_username
    from .jobs import map_status

    s = _site(name)
    claimed = body.username or s.username_claimed
    if not claimed:
        raise HTTPException(422, "site has no usernameClaimed; pass username")
    unclaimed = s.username_unclaimed or control_username()
    out = {}
    for label, u in (("claimed", claimed), ("unclaimed", unclaimed)):
        res = await engine_search(username=u, site_dict={s.name: s}, logger=S.mgr.engine_log, timeout=15,
                                  is_parsing_enabled=True, no_progressbar=True, forced=True, control_probe=True,
                                  site_stats=S.store.site_stats(),
                                  cloudflare_bypass=webgate_config(S.store.kv_get("settings", {}) or {}))
        st = next(iter(res.values()))["status"]
        out[label] = {
            "site": s.name, "username": u, "url": st.site_url_user, "status": map_status(st, 60),
            "confidence": getattr(st, "confidence", None) or 0, "reasons": getattr(st, "reasons", []) or [],
            "evidence": getattr(st, "evidence", {}) or {},
            "ids": {k: str(v) for k, v in (st.ids_data or {}).items()},
            "error": str(st.error) if getattr(st, "error", None) else None,
        }
    out["healthy"] = out["claimed"]["status"] == "claimed" and out["unclaimed"]["status"] in ("available", "uncertain")
    return out


@app.get("/api/tags", dependencies=A)
def tags():
    counts: Dict[str, int] = {}
    for s in S.db.sites:
        if s.disabled:
            continue
        for t in s.tags:
            counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda x: -x[1])]


# --------------------------------------------------------------------------- #
# settings / stats
# --------------------------------------------------------------------------- #
@app.get("/api/settings", dependencies=A)
def get_settings():
    st = S.store.kv_get("settings", {}) or {}
    return {"defaults": normalize_options(st.get("defaults")), "proxy": st.get("proxy", ""),
            "tor_proxy": st.get("tor_proxy", ""), "max_connections": st.get("max_connections", 50),
            "webgate": webgate_settings(st)}


@app.put("/api/settings", dependencies=A)
def put_settings(body: Dict[str, Any]):
    st = {
        "defaults": normalize_options(body.get("defaults")),
        "proxy": (body.get("proxy") or "").strip(),
        "tor_proxy": (body.get("tor_proxy") or "").strip(),
        "max_connections": max(5, min(200, int(body.get("max_connections") or 50))),
        "webgate": webgate_settings({"webgate": {**(body.get("webgate") or {}), "enabled": bool((body.get("webgate") or {}).get("enabled"))}}),
    }
    S.store.kv_set("settings", st)
    return st


# --------------------------------------------------------------------------- #
# webgate: FlareSolverr-style solver (Byparr / FlareSolverr)
# --------------------------------------------------------------------------- #
class WebgateRequest(BaseModel):
    url: str
    method: Literal["get", "post"] = "get"
    post_data: Optional[str] = None
    max_timeout_ms: Optional[int] = None
    include_body: bool = True


async def _solver_call(endpoint: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    from aiohttp import ClientSession, ClientTimeout
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as sess:
            async with sess.post(endpoint, json=payload) as r:
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    data = {"message": (await r.text())[:300]}
                if r.status >= 400:
                    raise HTTPException(502, f"solver {r.status}: {str(data.get('detail') or data.get('message') or data)[:300]}")
                return data
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(504, "solver timeout")
    except Exception as e:
        raise HTTPException(502, f"solver unreachable: {e}")


def _solver_endpoint():
    w = webgate_settings(S.store.kv_get("settings", {}) or {})
    if not w["url"]:
        raise HTTPException(409, "webgate url not configured")
    return w["url"], w["max_timeout_ms"]


@app.get("/api/webgate/status", dependencies=A)
async def webgate_status():
    """Health of the configured solver (GET <base>/health when available)."""
    w = webgate_settings(S.store.kv_get("settings", {}) or {})
    if not w["url"]:
        return {"configured": False, "enabled": False, "ok": False}
    base = w["url"].rsplit("/v1", 1)[0].rstrip("/")
    from aiohttp import ClientSession, ClientTimeout
    try:
        async with ClientSession(timeout=ClientTimeout(total=8)) as sess:
            async with sess.get(base + "/health") as r:
                data = await r.json(content_type=None)
                return {"configured": True, "enabled": w["enabled"], "ok": r.status == 200, "url": w["url"], "info": data}
    except Exception as e:
        return {"configured": True, "enabled": w["enabled"], "ok": False, "url": w["url"], "error": str(e)}


@app.post("/api/webgate/request", dependencies=A)
async def webgate_request(body: WebgateRequest):
    """FlareSolverr-style fetch: solve challenge and return status/url/cookies/UA/body."""
    scheme = urlparse(body.url).scheme
    if scheme not in ("http", "https"):
        raise HTTPException(400, "url must be http(s)")
    endpoint, default_ms = _solver_endpoint()
    ms = max(5000, min(120000, body.max_timeout_ms or default_ms))
    payload: Dict[str, Any] = {"cmd": f"request.{body.method}", "url": body.url, "maxTimeout": ms}
    if body.method == "post" and body.post_data is not None:
        payload["postData"] = body.post_data
    data = await _solver_call(endpoint, payload, ms / 1000 + 10)
    if data.get("status") != "ok":
        raise HTTPException(502, str(data.get("message") or "solver error"))
    sol = data.get("solution") or {}
    html_ = sol.get("response") or ""
    return {
        "status": int(sol.get("status") or 0), "url": sol.get("url"), "user_agent": sol.get("userAgent"),
        "cookies": sol.get("cookies") or [], "headers": sol.get("headers") or {},
        "body_length": len(html_), "body": html_ if body.include_body else None,
        "solver_version": data.get("version"),
        "elapsed_ms": (data.get("endTimestamp") or 0) - (data.get("startTimestamp") or 0) or None,
    }


@app.get("/api/stats", dependencies=A)
def stats():
    t = S.store.totals()
    return {
        "sites_total": len(S.db.sites),
        "sites_enabled": _enabled_count(),
        "sites_quarantined": sum(1 for s in S.db.sites if getattr(s, "quarantined", False)),
        **t,
    }


# --------------------------------------------------------------------------- #
# SPA
# --------------------------------------------------------------------------- #
if os.path.isdir(os.path.join(DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    f = os.path.join(DIST, path)
    if path and os.path.isfile(f) and os.path.realpath(f).startswith(os.path.realpath(DIST)):
        return FileResponse(f)
    idx = os.path.join(DIST, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    return JSONResponse({"error": "frontend not built; run `npm run build` in web/"}, status_code=503)


def main():
    import uvicorn
    host = _env("HOST", "127.0.0.1")
    port = int(_env("PORT", "7580"))
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
