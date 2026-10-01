"""Shared helpers for the site database tools.

data.json is read and written as plain JSON (not through MaigretDatabase) so the
tools never reshape fields they do not own: ``json.dumps(indent=4,
ensure_ascii=False)`` round-trips the upstream file byte-for-byte, which keeps
diffs limited to the sites a tool actually changed.

Provenance / health fields written by these tools (all optional, absent means
default):

* ``origin``           -- "wmn" (imported from WhatsMyName) or "wmn-merged"
                          (maigret entry whose detection was replaced by WMN's).
                          Absent means the entry is maigret's own. NOT ``source``:
                          that field already means "mirror of parent site".
* ``trancoRank``       -- rank of the registered domain in the Tranco top-1M list.
* ``quarantined``      -- true when verify_sites.py saw the check misbehave.
* ``quarantineReason`` -- short machine-readable reason.
* ``lastVerified``     -- ISO date (YYYY-MM-DD) of the last verification run.
"""

import json
import os
import re
from typing import Dict, Iterable, Optional
from urllib.parse import urlsplit

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON = os.path.join(REPO_ROOT, "maigret", "resources", "data.json")

ORIGIN_WMN = "wmn"
ORIGIN_WMN_MERGED = "wmn-merged"

_STRIP_PREFIXES = ("www.", "m.", "mobile.")


def load_db(path: str = DATA_JSON) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(data: dict, path: str = DATA_JSON) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=4, ensure_ascii=False))
    os.replace(tmp, path)


def url_host(url: Optional[str]) -> str:
    """Lower-case hostname of a URL template, with {placeholder} labels removed.

    ``https://{username}.tumblr.com/`` -> ``tumblr.com``.
    """
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    # urlsplit chokes on braces in the netloc only for ports; cut by hand.
    netloc = url.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0]
    netloc = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower().strip(".")
    labels = [l for l in netloc.split(".") if l and "{" not in l and "}" not in l]
    return ".".join(labels)


def normalize_domain(url: Optional[str]) -> str:
    """Host with cosmetic prefixes (www., m.) removed; used for dedupe."""
    host = url_host(url)
    changed = True
    while changed:
        changed = False
        for p in _STRIP_PREFIXES:
            if host.startswith(p) and host.count(".") >= 2:
                host = host[len(p):]
                changed = True
    return host


def site_main_url(site: dict) -> str:
    """Best guess of a site's main URL (urlMain, else derived from url)."""
    if site.get("urlMain"):
        return site["urlMain"]
    url = site.get("url") or ""
    if "{urlMain}" in url:
        return ""
    parts = urlsplit(url.replace("{username}", "x")) if "://" in url else None
    if parts and parts.netloc:
        return f"{parts.scheme}://{url_host(url)}/"
    return ""


def domain_candidates(host: str) -> Iterable[str]:
    """host, then each parent domain with >= 2 labels: a.b.example.co.uk ->
    a.b.example.co.uk, b.example.co.uk, example.co.uk, co.uk."""
    labels = host.split(".")
    for i in range(len(labels) - 1):
        yield ".".join(labels[i:])


def build_domain_index(sites: Dict[str, dict]) -> Dict[str, list]:
    """normalized domain -> site names. A site is indexed under its urlMain
    domain and under the hosts of url/urlProbe, since urlMain often names the
    bare domain while profiles live on a subdomain (cent.co vs beta.cent.co)."""
    index: Dict[str, list] = {}
    for name, site in sites.items():
        doms = {normalize_domain(site_main_url(site))}
        for key in ("url", "urlProbe"):
            tmpl = site.get(key) or ""
            if "://" in tmpl:
                doms.add(normalize_domain(tmpl))
        for dom in doms:
            if dom:
                index.setdefault(dom, []).append(name)
    return index


def effective_check_type(site: dict, engines: Dict[str, dict]) -> str:
    if site.get("checkType"):
        return site["checkType"]
    eng = engines.get(site.get("engine") or "", {})
    return eng.get("site", {}).get("checkType", "")


def effective_absence(site: dict, engines: Dict[str, dict]) -> list:
    eng = engines.get(site.get("engine") or "", {})
    return list(site.get("absenceStrs") or []) + list(
        eng.get("site", {}).get("absenceStrs") or []
    )


def is_weak_site(site: dict, engines: Dict[str, dict]) -> bool:
    """Disabled, or a status_code check with no absence markers (the main
    false-positive source: any 200 page counts as a found profile).

    status_code checks that probe a JSON API (urlProbe set by the site or its
    engine, e.g. Mastodon's accounts/lookup) are not weak: APIs return a real
    404 for a missing account, unlike HTML front-ends with soft 404 pages.
    """
    if site.get("disabled"):
        return True
    if effective_check_type(site, engines) != "status_code":
        return False
    if effective_absence(site, engines):
        return False
    eng = engines.get(site.get("engine") or "", {}).get("site", {})
    if eng.get("urlProbe"):
        return False
    return True


_SAFE_FORMAT = re.compile(r"\{(?!username\}|urlMain\}|urlSubpath\})[^{}]*\}")


def has_foreign_placeholders(template: str) -> bool:
    """True if str.format would trip over braces other than maigret's own."""
    return bool(_SAFE_FORMAT.search(template))
