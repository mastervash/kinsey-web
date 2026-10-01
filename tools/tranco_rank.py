#!/usr/bin/env python3
"""Set ``trancoRank`` on every site from the Tranco top-1M list.

    python tools/tranco_rank.py [--list FILE|URL] [--db PATH] [--dry-run]

The default Tranco list is ranked by pay-level (registered) domain, so a
site's host is looked up longest-suffix first (forum.example.co.uk ->
example.co.uk -> co.uk is never reached because a real registered domain
matches first). No public-suffix library is needed: the list itself only
contains registered domains.

Sites whose domain is not in the list lose any stale ``trancoRank``.
``alexaRank`` is left untouched. If the download fails the database is not
modified and the script exits 0 (the rank is optional metadata).
"""

import argparse
import csv
import io
import os
import sys
import urllib.request
import zipfile
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.sitedb_common import (  # noqa: E402
    DATA_JSON,
    domain_candidates,
    load_db,
    normalize_domain,
    save_db,
    site_main_url,
)

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"


def parse_tranco(raw: bytes) -> Dict[str, int]:
    """Accept the zip as published or a bare ``rank,domain`` CSV."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".csv"))
            raw = zf.read(name)
    ranks: Dict[str, int] = {}
    for row in csv.reader(io.StringIO(raw.decode("utf-8", "ignore"))):
        if len(row) < 2:
            continue
        try:
            rank = int(row[0])
        except ValueError:
            continue
        dom = row[1].strip().lower()
        if dom and dom not in ranks:
            ranks[dom] = rank
    return ranks


def lookup_rank(host: str, ranks: Dict[str, int]) -> Optional[int]:
    for cand in domain_candidates(host):
        if cand in ranks:
            return ranks[cand]
    return None


def fetch(src: str) -> bytes:
    if "://" in src:
        req = urllib.request.Request(src, headers={"User-Agent": "maigret-sitedb-tools"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    with open(src, "rb") as f:
        return f.read()


def apply_ranks(db: dict, ranks: Dict[str, int]) -> dict:
    stats = {"ranked": 0, "unranked": 0, "cleared": 0}
    for site in db["sites"].values():
        host = normalize_domain(site_main_url(site))
        rank = lookup_rank(host, ranks) if host else None
        if rank:
            site["trancoRank"] = rank
            stats["ranked"] += 1
        else:
            if site.pop("trancoRank", None) is not None:
                stats["cleared"] += 1
            stats["unranked"] += 1
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", default=TRANCO_URL, help="Tranco zip/CSV file or URL")
    ap.add_argument("--db", default=DATA_JSON)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        ranks = parse_tranco(fetch(args.list))
    except Exception as e:  # network, zip, anything: rank is optional
        print(f"Tranco list unavailable ({e.__class__.__name__}: {e}); skipping.")
        return 0
    if len(ranks) < 1000:
        print(f"Tranco list suspiciously small ({len(ranks)} rows); skipping.")
        return 0

    db = load_db(args.db)
    stats = apply_ranks(db, ranks)
    print(f"tranco domains: {len(ranks)}")
    print(f"sites ranked: {stats['ranked']}, unranked: {stats['unranked']}, cleared: {stats['cleared']}")
    if not args.dry_run:
        save_db(db, args.db)
        print(f"wrote {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
