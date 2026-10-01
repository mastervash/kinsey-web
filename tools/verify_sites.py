#!/usr/bin/env python3
"""Verify site checks against live sites and quarantine the broken ones.

    python tools/verify_sites.py [--limit N] [--tag TAG ...] [--sites NAME ...]
                                 [--concurrency 50] [--timeout 10] [--apply]

For every enabled, non-disabled site three checks run through maigret's own
engine (maigret.checking.check_site_for_username, so headers, probes, POST
payloads, engines, curl_cffi for tls_fingerprint sites and error-page
detection all behave exactly as in a real scan):

  * usernameClaimed       -> must be CLAIMED
  * usernameUnclaimed     -> must NOT be CLAIMED
  * random 12-char string -> must NOT be CLAIMED

An UNKNOWN result (timeout, block page, 5xx) on the claimed probe is retried
once ("run twice"), so a transient hiccup is not taken for a broken check.

Verdicts (``quarantineReason``):
  dead               DNS failure / connection refused on every probe
  claimed_not_found  known account reported as not found
  false_positive     unclaimed or random name reported as found
  error              persistent errors (bot protection, 403, timeouts)

Report goes to tools/verify_report.json. With --apply the database gets, per
checked site: ``lastVerified`` (ISO date); failing sites get
``quarantined: true`` + ``quarantineReason``; passing sites lose both.
Sites with verdict ``error`` only get ``lastVerified`` unless
--quarantine-errors is given: a block page says nothing about the check.
``disabled`` is never touched.

Site order is by trancoRank, then alexaRank (most popular first), so --limit N
checks the top N.
"""

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import random
import string
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.sitedb_common import DATA_JSON, load_db, save_db  # noqa: E402

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_report.json")

DEAD = "dead"
CLAIMED_NOT_FOUND = "claimed_not_found"
FALSE_POSITIVE = "false_positive"
ERROR = "error"
OK = "ok"

# CheckError types (maigret.checking) meaning the host itself is unreachable.
_DEAD_ERROR_TYPES = ("Connecting failure", "Connecting failure (DNS)", "SSL")


def random_username(rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    return "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(12))


def classify(probes: Dict[str, Dict[str, Any]]) -> str:
    """Pure verdict from probe outcomes.

    probes: role -> {"status": "CLAIMED"|"AVAILABLE"|"UNKNOWN"|"ILLEGAL",
                     "error_type": str|None}
    roles: "claimed", "unclaimed", "random".
    """
    vals = list(probes.values())
    if vals and all(
        p["status"] == "UNKNOWN" and (p.get("error_type") or "").startswith(_DEAD_ERROR_TYPES)
        for p in vals
    ):
        return DEAD
    for role in ("unclaimed", "random"):
        if probes.get(role, {}).get("status") == "CLAIMED":
            return FALSE_POSITIVE
    c = probes.get("claimed", {})
    if c.get("status") == "AVAILABLE":
        return CLAIMED_NOT_FOUND
    if c.get("status") == "CLAIMED":
        # negatives must not be errors-only either: an unknown negative is not
        # proof of a working check, but it is not a failure of it.
        return OK
    return ERROR


def select_sites(db, limit=None, tags=None, names=None) -> List[Any]:
    tags = [t.lower() for t in tags or []]
    names_l = [n.lower() for n in names or []]
    out = []
    for s in db.sites:
        if s.disabled or s.type != "username":
            continue
        if s.protocol in ("tor", "i2p", "dns"):
            continue
        if names_l and s.name.lower() not in names_l:
            continue
        if tags and not set(t.lower() for t in s.tags) & set(tags):
            continue
        if not s.username_claimed:
            continue
        out.append(s)

    def key(s):
        tr = getattr(s, "tranco_rank", None) or sys.maxsize
        ar = s.alexa_rank or sys.maxsize
        return (min(tr, ar if ar != sys.maxsize else sys.maxsize), s.name)

    out.sort(key=key)
    return out[:limit] if limit else out


class _Engine:
    """Runs one probe through maigret's checking pipeline."""

    def __init__(self, timeout: float):
        from maigret import checking

        self.checking = checking
        self.timeout = timeout
        self.logger = logging.getLogger("verify_sites")
        self.logger.setLevel(logging.CRITICAL)
        self.options = {
            "cookies": None,
            "parsing": False,
            "enrich": False,
            "enrich_requests": 0,
            "timeout": timeout,
            "id_type": "username",
            "forced": True,
            "cloudflare_bypass": None,
            "proxy": None,
            "checkers": {
                "": self._clearweb,
                "tor": checking.CheckerMock,
                "i2p": checking.CheckerMock,
                "dns": checking.CheckerMock,
            },
        }

    def _clearweb(self):
        return self.checking.SimpleAiohttpChecker(logger=self.logger)

    async def probe(self, site, username: str) -> Dict[str, Any]:
        # check_site_for_username mutates per-site state (stats, url_main on
        # mirror retry); a shallow copy keeps concurrent probes independent.
        import copy

        s = copy.copy(site)
        s.stats = {}
        started = time.monotonic()
        try:
            _, res = await asyncio.wait_for(
                self.checking.check_site_for_username(
                    s, username, self.options, self.logger, Mock()
                ),
                timeout=self.timeout * 3 + 5,
            )
            st = res.get("status")
            checker = res.get("checker")
            if checker is not None and hasattr(checker, "close"):
                try:
                    await checker.close()
                except Exception:
                    pass
            err = getattr(st, "error", None)
            return {
                "username": username,
                "status": st.status.name if st else "UNKNOWN",
                "error_type": getattr(err, "type", None),
                "error": str(err) if err else None,
                "http_status": res.get("http_status"),
                "seconds": round(time.monotonic() - started, 2),
            }
        except asyncio.TimeoutError:
            return {"username": username, "status": "UNKNOWN", "error_type": "Request timeout",
                    "error": "hard timeout", "http_status": None,
                    "seconds": round(time.monotonic() - started, 2)}
        except Exception as e:  # never let one site kill the run
            return {"username": username, "status": "UNKNOWN", "error_type": "Unexpected",
                    "error": f"{e.__class__.__name__}: {e}"[:300], "http_status": None,
                    "seconds": round(time.monotonic() - started, 2)}


async def verify_site(engine: _Engine, site, sem: asyncio.Semaphore, rng: random.Random) -> Dict[str, Any]:
    async with sem:
        probes: Dict[str, Dict[str, Any]] = {}
        probes["claimed"] = await engine.probe(site, site.username_claimed)
        if probes["claimed"]["status"] == "UNKNOWN" and not (
            probes["claimed"].get("error_type") or ""
        ).startswith(_DEAD_ERROR_TYPES):
            probes["claimed"] = await engine.probe(site, site.username_claimed)
        unclaimed = site.username_unclaimed or "noonewouldeverusethis7"
        probes["unclaimed"], probes["random"] = await asyncio.gather(
            engine.probe(site, unclaimed),
            engine.probe(site, random_username(rng)),
        )
        # Repeat a false positive once: rate limiting can serve a 200 page.
        for role in ("unclaimed", "random"):
            if probes[role]["status"] == "CLAIMED":
                again = await engine.probe(site, probes[role]["username"])
                if again["status"] != "CLAIMED":
                    probes[role] = again
        verdict = classify(probes)
        return {"name": site.name, "verdict": verdict, "probes": probes}


VERDICT_SCORE = {OK: 3, ERROR: 1, DEAD: 0, CLAIMED_NOT_FOUND: 0, FALSE_POSITIVE: 0}


def verify_site_dicts(site_dicts: Dict[str, dict], engines: Dict[str, dict], concurrency=50,
                      timeout=10.0, seed=None) -> Dict[str, str]:
    """Verify raw data.json site dicts (disabled flag ignored); name -> verdict."""
    from maigret.sites import MaigretDatabase

    sites = {}
    for name, d in site_dicts.items():
        d = dict(d)
        d.pop("disabled", None)
        sites[name] = d
    db = MaigretDatabase().load_from_json({"sites": sites, "engines": engines, "tags": []})
    todo = [s for s in db.sites if s.type == "username" and s.protocol not in ("tor", "i2p", "dns")]
    res = asyncio.run(run(todo, concurrency, timeout, seed))
    return {r["name"]: r["verdict"] for r in res}


async def run(sites, concurrency: int, timeout: float, seed: Optional[int] = None) -> List[Dict[str, Any]]:
    engine = _Engine(timeout)
    sem = asyncio.Semaphore(concurrency)
    rng = random.Random(seed)
    tasks = [asyncio.create_task(verify_site(engine, s, sem, rng)) for s in sites]
    results = []
    done = 0
    for fut in asyncio.as_completed(tasks):
        results.append(await fut)
        done += 1
        if done % 25 == 0 or done == len(tasks):
            print(f"  {done}/{len(tasks)}", file=sys.stderr, flush=True)
    results.sort(key=lambda r: r["name"])
    return results


def apply_results(data: dict, results: List[Dict[str, Any]], today: str, quarantine_errors=False) -> Dict[str, int]:
    counts = {"quarantined": 0, "cleared": 0, "verified": 0}
    for r in results:
        site = data["sites"].get(r["name"])
        if site is None:
            continue
        site["lastVerified"] = today
        counts["verified"] += 1
        v = r["verdict"]
        if v == OK:
            if site.pop("quarantined", None):
                counts["cleared"] += 1
            site.pop("quarantineReason", None)
        elif v == ERROR and not quarantine_errors:
            continue
        else:
            site["quarantined"] = True
            site["quarantineReason"] = v
            counts["quarantined"] += 1
    return counts


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    by: Dict[str, int] = {}
    for r in results:
        by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    n = len(results) or 1
    return {
        "total": len(results),
        "by_verdict": by,
        "pass_rate": round(by.get(OK, 0) / n, 4),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DATA_JSON)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--sites", nargs="+", default=[])
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--report", default=REPORT_PATH)
    ap.add_argument("--apply", action="store_true", help="write quarantine flags into the DB")
    ap.add_argument("--quarantine-errors", action="store_true",
                    help="also quarantine sites whose probes only error out")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    from maigret.sites import MaigretDatabase

    db = MaigretDatabase().load_from_path(args.db)
    sites = select_sites(db, args.limit, args.tag, args.sites)
    print(f"verifying {len(sites)} sites (concurrency {args.concurrency}, timeout {args.timeout}s)",
          file=sys.stderr)
    started = time.monotonic()
    results = asyncio.run(run(sites, args.concurrency, args.timeout, args.seed))
    elapsed = round(time.monotonic() - started, 1)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    summary = summarize(results)
    summary["seconds"] = elapsed
    summary["date"] = today

    report = {"summary": summary, "results": results}
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(summary, indent=2))

    if args.apply:
        data = load_db(args.db)
        counts = apply_results(data, results, today, args.quarantine_errors)
        save_db(data, args.db)
        print(f"applied: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
