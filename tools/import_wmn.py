#!/usr/bin/env python3
"""Import WhatsMyName (WMN) sites into maigret's data.json.

    python tools/import_wmn.py [--wmn FILE|URL] [--db PATH] [--dry-run] [--report FILE]

Conversion (see ``convert_wmn_entry``):
  * url        = uri_pretty or uri_check, {account} -> {username}
  * urlProbe   = uri_check when it differs from url
  * checkType  = "message"; presenseStrs [e_string]; absenceStrs [m_string] if set
  * POST checks (post_body) become requestMethod/requestPayload when the body is
    a flat JSON object or a form body whose values maigret can .format(); any
    other body (GraphQL with braces, nested JSON) is skipped.
  * tags from ``cat`` (``xx NSFW xx`` -> ``nsfw``), ``origin: "wmn"``.

Dedupe is by normalized domain (urlMain plus url/urlProbe hosts), falling back
to an exact name match. When maigret already covers the domain:
  * every maigret site there has a real check -> skip the WMN entry;
  * a maigret site there is disabled, or is a status_code check without
    absenceStrs and without an engine API probe -> its detection fields are
    replaced by WMN's, the name, tags and rank are kept and it is marked
    ``origin: "wmn-merged"``.
With --verify-merges both versions of every merged site are live-checked and
the merge is reverted when WMN's check fails or verifies worse; reverted names
are remembered in tools/wmn_merge_rejects.json so later runs (with or without
--verify-merges) never merge them again.
Re-running is idempotent: entries already imported (origin wmn / wmn-merged)
are refreshed in place.

NSFW entries (cat ``xx NSFW xx``) get tag ``nsfw`` and ``nsfw: true``.
"""

import argparse
import copy
import json
import os
import re
import sys
import urllib.request
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.sitedb_common import (  # noqa: E402
    DATA_JSON,
    ORIGIN_WMN,
    ORIGIN_WMN_MERGED,
    build_domain_index,
    has_foreign_placeholders,
    is_weak_site,
    load_db,
    normalize_domain,
    save_db,
    url_host,
)

WMN_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
UNCLAIMED = "noonewouldeverusethis7"
REJECTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wmn_merge_rejects.json")

# WMN category -> maigret tags. Unlisted categories (misc) add no tag.
CAT_TO_TAGS: Dict[str, List[str]] = {
    "social": ["social"],
    "gaming": ["gaming"],
    "tech": ["tech"],
    "hobby": ["hobby"],
    "coding": ["coding"],
    "xx NSFW xx": ["nsfw"],
    "finance": ["finance"],
    "business": ["business"],
    "music": ["music"],
    "shopping": ["shopping"],
    "images": ["photo"],
    "blog": ["blog"],
    "art": ["art"],
    "dating": ["dating"],
    "political": ["discussion", "social"],
    "video": ["video"],
    "news": ["news"],
    "archived": ["archive"],
    "health": ["medicine"],
}
NEW_TAGS = ["nsfw"]
NSFW_CAT = "xx NSFW xx"

# Detection fields owned by the check. On merge every one of these is removed
# from the maigret entry before WMN's are applied, so no stale engine/probe
# setting survives to contradict the new check.
DETECTION_FIELDS = (
    "url",
    "urlProbe",
    "urlSubpath",
    "checkType",
    "presenseStrs",
    "absenceStrs",
    "usernameClaimed",
    "usernameUnclaimed",
    "requestMethod",
    "requestPayload",
    "requestHeadOnly",
    "getParams",
    "headers",
    "engine",
    "errorUrl",
    "ignore403",
    "disabled",
    "activation",
)

# Status codes maigret's detect_error_page turns into an error, so a check that
# relies on them can never report CLAIMED (e_code) or AVAILABLE (m_code).
_ERROR_CODES = {401, 403, 429}


def _is_error_code(code) -> bool:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    return code in _ERROR_CODES or code >= 500


def _sub_account(s: str) -> str:
    return s.replace("{account}", "{username}")


def _url_main(url: str) -> str:
    scheme = url.split("://", 1)[0] if "://" in url else "https"
    return f"{scheme}://{url_host(url)}/"


def _convert_payload(body: str, headers: dict) -> Tuple[Optional[dict], Optional[dict], str]:
    """Return (payload, headers, reason). payload None means unsupported.

    maigret sends request_payload as JSON unless Content-Type is exactly
    ``application/x-www-form-urlencoded``, and formats only top-level string
    values with ``str.format(username=...)``.
    """
    headers = dict(headers or {})
    ctype_key = next((k for k in headers if k.lower() == "content-type"), None)
    ctype = (headers.get(ctype_key) or "").lower() if ctype_key else ""
    payload: Optional[dict] = None
    if "x-www-form-urlencoded" in ctype:
        pairs = parse_qsl(body, keep_blank_values=True)
        if not pairs:
            return None, None, "post_body: empty form"
        payload = {k: _sub_account(v) for k, v in pairs}
        if ctype_key:
            del headers[ctype_key]
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        try:
            parsed = json.loads(body)
        except ValueError:
            return None, None, "post_body: not JSON"
        if not isinstance(parsed, dict):
            return None, None, "post_body: not a JSON object"
        payload = {}
        for k, v in parsed.items():
            if isinstance(v, str):
                payload[k] = _sub_account(v)
            elif isinstance(v, (int, float, bool)) or v is None:
                payload[k] = v
            else:
                # Nested values are sent verbatim by maigret, so {account}
                # inside them would never be substituted.
                if "{account}" in json.dumps(v):
                    return None, None, "post_body: username in nested JSON"
                payload[k] = v
    for v in payload.values():
        if isinstance(v, str) and has_foreign_placeholders(v):
            return None, None, "post_body: braces maigret cannot format"
    if not any(isinstance(v, str) and "{username}" in v for v in payload.values()):
        return None, None, "post_body: no username field"
    return payload, headers, ""


def convert_wmn_entry(entry: dict) -> Tuple[Optional[str], Optional[dict], str]:
    """Convert one WMN entry to (name, maigret site dict, skip_reason).

    Pure function. On skip returns (name, None, reason).
    """
    name = (entry.get("name") or "").strip()
    if not name:
        return None, None, "no name"
    if entry.get("valid") is False:
        return name, None, "marked invalid in WMN"
    uri_check = entry.get("uri_check") or ""
    if "{account}" not in uri_check and not entry.get("post_body"):
        return name, None, "uri_check has no {account}"
    known = [k for k in (entry.get("known") or []) if k]
    if not known:
        return name, None, "no known username"
    e_string = entry.get("e_string") or ""
    m_string = entry.get("m_string") or ""
    if not e_string.strip() and not m_string.strip():
        return name, None, "no e_string/m_string"
    e_code, m_code = entry.get("e_code"), entry.get("m_code")
    if _is_error_code(e_code):
        return name, None, f"e_code {e_code} is an error status in maigret"
    try:
        if 300 <= int(e_code) < 400:
            # message checks follow redirects, so a 3xx "found" page is never seen
            return name, None, f"e_code {e_code} redirect"
    except (TypeError, ValueError):
        pass
    if _is_error_code(m_code) and not m_string.strip():
        return name, None, f"m_code {m_code} is an error status in maigret"

    pretty = entry.get("uri_pretty") or ""
    url = _sub_account(pretty or uri_check)
    probe = _sub_account(uri_check)
    if "{username}" not in url:
        url = probe if "{username}" in probe else url
    for tmpl in (url, probe):
        if has_foreign_placeholders(tmpl):
            return name, None, "URL has braces maigret cannot format"

    site: dict = {}
    site["url"] = url
    site["urlMain"] = _url_main(url)
    if probe != url:
        site["urlProbe"] = probe
    site["checkType"] = "message"
    if e_string.strip():
        site["presenseStrs"] = [e_string]
    if m_string.strip():
        site["absenceStrs"] = [m_string]

    headers = dict(entry.get("headers") or {})
    if entry.get("post_body"):
        payload, headers, reason = _convert_payload(entry["post_body"], headers)
        if payload is None:
            return name, None, reason
        site["requestMethod"] = "POST"
        site["requestPayload"] = payload
    if headers:
        site["headers"] = headers

    bad = entry.get("strip_bad_char") or ""
    if bad:
        site["regexCheck"] = "^[^" + re.escape(bad) + "]+$"
        if not re.search(site["regexCheck"], known[0]):
            del site["regexCheck"]

    site["usernameClaimed"] = known[0]
    site["usernameUnclaimed"] = UNCLAIMED
    tags = list(CAT_TO_TAGS.get(entry.get("cat") or "", []))
    if tags:
        site["tags"] = tags
    if entry.get("cat") == NSFW_CAT:
        site["nsfw"] = True
    site["origin"] = ORIGIN_WMN
    return name, site, ""


def merge_into(existing: dict, wmn_site: dict) -> dict:
    """Replace the detection fields of a maigret entry with WMN's.

    Keeps name (caller), urlMain, tags (union), alexaRank, protection, errors
    and any metadata. Returns a new dict.
    """
    merged = {k: v for k, v in existing.items() if k not in DETECTION_FIELDS}
    for k, v in wmn_site.items():
        if k in ("tags", "origin", "urlMain", "regexCheck"):
            continue
        merged[k] = v
    if not merged.get("urlMain"):
        merged["urlMain"] = wmn_site["urlMain"]
    # A maigret username regex is site knowledge worth keeping, unless it
    # rejects WMN's known account (then it is wrong for the new check).
    regex = merged.get("regexCheck") or wmn_site.get("regexCheck")
    if regex:
        try:
            ok = re.search(regex, merged["usernameClaimed"]) and re.search(
                regex, merged["usernameUnclaimed"]
            )
        except re.error:
            ok = False
        if ok:
            merged["regexCheck"] = regex
        else:
            merged.pop("regexCheck", None)
    tags = list(existing.get("tags") or [])
    for t in wmn_site.get("tags") or []:
        if t not in tags:
            tags.append(t)
    if tags:
        merged["tags"] = tags
    merged["origin"] = ORIGIN_WMN_MERGED
    return merged


def import_wmn(db: dict, wmn: dict, rejects: Optional[set] = None) -> dict:
    """Mutate ``db`` in place; return stats with per-site lists.

    ``rejects``: maigret site names whose merge a previous --verify-merges run
    reverted (maigret's check verified better); they are never merged again.
    """
    rejects = set(rejects or ())
    sites: Dict[str, dict] = db["sites"]
    engines = db.get("engines", {})
    # Index is built from the pre-import DB: WMN entries never dedupe against
    # each other, only against maigret's own coverage.
    index = build_domain_index(sites)
    names_lower = {n.lower(): n for n in sites}
    stats: dict = {
        "wmn_total": 0,
        "added": [],
        "merged": [],
        "refreshed": [],
        "skipped_existing": [],
        "skipped_unsupported": [],
    }
    merged_targets = set()
    for entry in wmn.get("sites", []):
        stats["wmn_total"] += 1
        name, site, reason = convert_wmn_entry(entry)
        if site is None:
            stats["skipped_unsupported"].append({"name": name, "reason": reason})
            continue
        domain = normalize_domain(site["urlMain"])
        matches = list(index.get(domain, []))
        if not matches:
            # Same service under another host (shikimori.one vs .io,
            # venmo.com vs account.venmo.com): a name match is the signal.
            same_name = names_lower.get(name.lower())
            if same_name and sites[same_name].get("origin") != ORIGIN_WMN:
                matches = [same_name]

        # Idempotent re-run: refresh what a previous run imported.
        prev = next(
            (
                m
                for m in matches + ([name] if name in sites else [])
                if sites[m].get("origin") == ORIGIN_WMN
                and normalize_domain(sites[m].get("urlMain")) == domain
                and m.split(" (WMN)")[0] == name
            ),
            None,
        )
        if prev:
            keep = {k: v for k, v in sites[prev].items() if k not in DETECTION_FIELDS}
            keep.update({k: v for k, v in site.items()})
            sites[prev] = keep
            stats["refreshed"].append(prev)
            continue
        prev_merged = [m for m in matches if sites[m].get("origin") == ORIGIN_WMN_MERGED]

        if not matches:
            target_name = name
            if target_name in sites:
                target_name = f"{name} (WMN)"
            if target_name in sites:
                stats["skipped_existing"].append(
                    {"name": name, "reason": "name taken", "domain": domain}
                )
                continue
            sites[target_name] = site
            stats["added"].append(target_name)
            continue

        weak = [
            m
            for m in matches
            if m not in merged_targets
            and m not in rejects
            and is_weak_site(sites[m], engines)
        ]
        strong = [m for m in matches if m not in weak and m not in prev_merged]
        if prev_merged:
            # Same WMN entry merged on an earlier run: refresh that entry
            # instead of merging into a second weak duplicate. merge_into
            # copied WMN's url, so the url identifies which entry it was.
            same = [
                m
                for m in prev_merged
                if m not in merged_targets and sites[m].get("url") == site["url"]
            ]
            if same:
                tgt = same[0]
                sites[tgt] = merge_into(sites[tgt], site)
                merged_targets.add(tgt)
                stats["refreshed"].append(tgt)
                continue
        if not weak:
            if strong:
                reason = "covered by " + ", ".join(strong[:3])
            else:
                reason = "domain already merged from another WMN entry: " + ", ".join(
                    matches[:3]
                )
            stats["skipped_existing"].append({"name": name, "reason": reason, "domain": domain})
            continue
        target = _pick_merge_target(weak, sites, site)
        if not target:
            stats["skipped_existing"].append(
                {"name": name, "reason": "ambiguous: " + ", ".join(weak[:3]), "domain": domain}
            )
            continue
        was = "disabled" if sites[target].get("disabled") else "status_code w/o absenceStrs"
        sites[target] = merge_into(sites[target], site)
        merged_targets.add(target)
        stats["merged"].append({"name": target, "wmn": name, "was": was})

    tags = db.setdefault("tags", [])
    used = {t for s in sites.values() for t in s.get("tags") or []}
    for t in NEW_TAGS:
        if t in used and t not in tags:
            tags.append(t)
    tags.sort()
    return stats


def _path_key(url: str) -> str:
    u = (url or "").split("://", 1)[-1]
    path = u.split("/", 1)[1] if "/" in u else ""
    return path.split("?", 1)[0].rstrip("/").lower()


def _pick_merge_target(candidates: List[str], sites: Dict[str, dict], wmn_site: dict) -> Optional[str]:
    if len(candidates) == 1:
        return candidates[0]
    want = _path_key(wmn_site["url"])
    same = [c for c in candidates if _path_key(sites[c].get("url", "")) == want]
    if len(same) == 1:
        return same[0]
    return None


def should_keep_merge(old_verdict: Optional[str], new_verdict: Optional[str]) -> bool:
    """Keep WMN's check unless it fails outright or maigret's own verified
    strictly better.

    Ties go to WMN: a message check with absence markers is never weaker than
    the status_code-without-absenceStrs (or disabled) entry it replaced. A WMN
    check that fails outright (score 0) is never kept: the merge would also
    drop ``disabled`` and so re-enable a broken site.
    """
    from tools.verify_sites import VERDICT_SCORE

    old = VERDICT_SCORE.get(old_verdict or "", 0)
    new = VERDICT_SCORE.get(new_verdict or "", 0)
    return new > 0 and new >= old


def gate_merges(db: dict, original: Dict[str, dict], stats: dict) -> None:
    from tools.verify_sites import verify_site_dicts

    names = [m["name"] for m in stats["merged"]]
    engines = db.get("engines", {})
    print(f"verifying {len(names)} merges (old vs new)...", file=sys.stderr)
    new_v = verify_site_dicts({n: db["sites"][n] for n in names}, engines, seed=1)
    old_v = verify_site_dicts({n: original[n] for n in names}, engines, seed=1)
    kept, reverted = [], []
    for m in stats["merged"]:
        n = m["name"]
        m["verdict_old"], m["verdict_new"] = old_v.get(n), new_v.get(n)
        if should_keep_merge(old_v.get(n), new_v.get(n)):
            kept.append(m)
        else:
            db["sites"][n] = original[n]
            reverted.append(m)
    stats["merged"] = kept
    stats["merge_reverted"] = reverted
    print(f"merges kept: {len(kept)}, reverted (maigret check verified better): {len(reverted)}")


def fetch_wmn(src: str) -> dict:
    if "://" in src:
        req = urllib.request.Request(src, headers={"User-Agent": "maigret-sitedb-tools"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(src, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wmn", default=WMN_URL, help="WMN JSON file or URL")
    ap.add_argument("--db", default=DATA_JSON)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default=None, help="write full stats JSON here")
    ap.add_argument(
        "--rejects",
        default=REJECTS_PATH,
        help="JSON list of maigret sites never to merge (written by --verify-merges)",
    )
    ap.add_argument(
        "--verify-merges",
        action="store_true",
        help="live-check both the maigret and the WMN version of every merged "
        "site and keep the maigret check where it verifies better (network)",
    )
    args = ap.parse_args(argv)

    db = load_db(args.db)
    original = copy.deepcopy(db["sites"])
    before = len(db["sites"])
    before_disabled = sum(1 for s in db["sites"].values() if s.get("disabled"))
    wmn = fetch_wmn(args.wmn)
    rejects_path = args.rejects
    rejects = set()
    if os.path.exists(rejects_path):
        with open(rejects_path, "r", encoding="utf-8") as f:
            rejects = set(json.load(f))
    stats = import_wmn(db, wmn, rejects)
    if args.verify_merges and stats["merged"]:
        gate_merges(db, original, stats)
        rejects |= {m["name"] for m in stats["merge_reverted"]}
        if not args.dry_run:
            with open(rejects_path, "w", encoding="utf-8") as f:
                json.dump(sorted(rejects), f, indent=2, ensure_ascii=False)
                f.write("\n")
    after = len(db["sites"])
    after_disabled = sum(1 for s in db["sites"].values() if s.get("disabled"))

    print(f"WMN entries:          {stats['wmn_total']}")
    print(f"added:                {len(stats['added'])}")
    print(f"merged:               {len(stats['merged'])}")
    print(f"refreshed:            {len(stats['refreshed'])}")
    print(f"skipped (covered):    {len(stats['skipped_existing'])}")
    print(f"skipped (unsupported):{len(stats['skipped_unsupported'])}")
    print(f"sites:    {before} -> {after}")
    print(f"disabled: {before_disabled} -> {after_disabled}")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
    if not args.dry_run:
        save_db(db, args.db)
        print(f"wrote {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
