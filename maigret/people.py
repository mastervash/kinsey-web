"""Person (real name / email) to username candidates.

Two sources:
  1. Permutations of the name (first.last, flast, ...) ranked by how often
     people actually pick that pattern.
  2. Public directory searches that index display names (GitHub, GitLab,
     Bluesky, Mastodon, Keybase, Hacker News, Gravatar for email).

Candidates found in directories are scored against optional context
(location, employer, school, keywords) so the scanner spends its budget on
the right person, not on every "John Smith".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from urllib.parse import quote

import aiohttp

log = logging.getLogger("maigret.people")

NICKNAMES: Dict[str, List[str]] = {
    "alexander": ["alex", "xander", "sasha"], "alexandra": ["alex", "sasha", "lexi"],
    "andrew": ["andy", "drew"], "anthony": ["tony"], "benjamin": ["ben", "benji"],
    "catherine": ["cathy", "kate", "katie"], "katherine": ["kate", "katie", "kathy"],
    "charles": ["charlie", "chuck"], "christopher": ["chris", "topher"],
    "christina": ["chris", "tina"], "daniel": ["dan", "danny"], "david": ["dave"],
    "edward": ["ed", "eddie", "ted"], "elizabeth": ["liz", "beth", "lizzy", "eliza"],
    "francis": ["frank"], "frederick": ["fred"], "gregory": ["greg"],
    "jacob": ["jake"], "james": ["jim", "jimmy", "jamie"], "jennifer": ["jen", "jenny"],
    "jessica": ["jess"], "jonathan": ["jon"], "john": ["jack", "johnny"],
    "joseph": ["joe", "joey"], "joshua": ["josh"], "kenneth": ["ken", "kenny"],
    "lawrence": ["larry"], "margaret": ["maggie", "meg", "peggy"], "matthew": ["matt"],
    "michael": ["mike", "mikey"], "nicholas": ["nick", "nicky"], "patricia": ["pat", "trish"],
    "patrick": ["pat"], "peter": ["pete"], "rebecca": ["becca", "becky"],
    "richard": ["rich", "rick", "dick"], "robert": ["rob", "bob", "bobby", "robbie"],
    "ronald": ["ron"], "samantha": ["sam"], "samuel": ["sam"], "stephen": ["steve"],
    "steven": ["steve"], "susan": ["sue"], "theodore": ["ted", "theo"],
    "thomas": ["tom", "tommy"], "timothy": ["tim"], "victoria": ["vicky", "tori"],
    "william": ["will", "bill", "billy", "liam"], "zachary": ["zach", "zack"],
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 \-']", "", s.lower()).replace("'", "")


def split_name(full: str):
    parts = [p for p in re.split(r"[\s\-]+", fold(full)) if p]
    if not parts:
        return None, [], None
    if len(parts) == 1:
        return parts[0], [], None
    return parts[0], parts[1:-1], parts[-1]


def name_permutations(full: str, limit: int = 40) -> List[Dict]:
    """Ranked username guesses for a real name. Higher weight first."""
    first, middles, last = split_name(full)
    if not first:
        return []
    out: Dict[str, float] = {}

    def add(u: str, w: float):
        u = u.strip("._-")
        if 3 <= len(u) <= 30 and (u not in out or out[u] < w):
            out[u] = w

    firsts = [(first, 1.0)] + [(n, 0.6) for n in NICKNAMES.get(first, [])]
    if not last:
        for f, w in firsts:
            add(f, 0.5 * w)
        return [{"username": u, "weight": round(w, 3)} for u, w in sorted(out.items(), key=lambda x: -x[1])][:limit]

    mi = middles[0][0] if middles else ""
    for f, w in firsts:
        add(f + last, 1.0 * w)
        add(f + "." + last, 0.95 * w)
        add(f + "_" + last, 0.85 * w)
        add(f + "-" + last, 0.6 * w)
        add(f[0] + last, 0.9 * w)
        add(f[0] + "." + last, 0.6 * w)
        add(f + last[0], 0.7 * w)
        add(last + f, 0.65 * w)
        add(last + "." + f, 0.55 * w)
        add(last + f[0], 0.55 * w)
        add(last + "_" + f, 0.45 * w)
        if mi:
            add(f + mi + last, 0.6 * w)
            add(f[0] + mi + last, 0.55 * w)
            add(f + "." + mi + "." + last, 0.45 * w)
        add("the" + f + last, 0.3 * w)
        add("real" + f + last, 0.3 * w)
        add(f + last + "official", 0.2 * w)
    ranked = sorted(out.items(), key=lambda x: -x[1])
    return [{"username": u, "weight": round(w, 3)} for u, w in ranked[:limit]]


@dataclass
class Candidate:
    username: str
    score: float = 0.0
    sources: List[str] = field(default_factory=list)
    display_name: Optional[str] = None
    profile_url: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None
    company: Optional[str] = None

    def public(self) -> dict:
        d = asdict(self)
        d["score"] = round(self.score, 1)
        return d


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"


async def _get_json(session: aiohttp.ClientSession, url: str, **kw):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12), **kw) as r:
            if r.status != 200:
                log.debug("people: %s -> %s", url, r.status)
                return None
            return await r.json(content_type=None)
    except Exception as e:
        log.debug("people: %s failed %s", url, e)
        return None


async def src_github(s, name):
    data = await _get_json(s, f"https://api.github.com/search/users?q={quote(name)}+in:name&per_page=10")
    out = []
    for it in (data or {}).get("items", [])[:10]:
        prof = await _get_json(s, it["url"]) or {}
        out.append(Candidate(it["login"], sources=["github"], display_name=prof.get("name"),
                             profile_url=it.get("html_url"), avatar=it.get("avatar_url"),
                             bio=prof.get("bio"), location=prof.get("location"), company=prof.get("company")))
    return out


async def src_gitlab(s, name):
    data = await _get_json(s, f"https://gitlab.com/api/v4/users?search={quote(name)}&per_page=10")
    return [Candidate(u["username"], sources=["gitlab"], display_name=u.get("name"),
                      profile_url=u.get("web_url"), avatar=u.get("avatar_url"))
            for u in (data or [])[:10] if isinstance(u, dict) and u.get("username")]


async def src_bluesky(s, name):
    data = await _get_json(s, f"https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors?q={quote(name)}&limit=10")
    out = []
    for a in (data or {}).get("actors", []):
        handle = a.get("handle", "")
        out.append(Candidate(handle.split(".")[0], sources=["bluesky"], display_name=a.get("displayName"),
                             profile_url=f"https://bsky.app/profile/{handle}", avatar=a.get("avatar"),
                             bio=(a.get("description") or "")[:300]))
    return out


async def src_mastodon(s, name):
    data = await _get_json(s, f"https://mastodon.social/api/v2/search?q={quote(name)}&type=accounts&limit=10")
    out = []
    for a in (data or {}).get("accounts", []):
        bio = re.sub(r"<[^>]+>", " ", a.get("note") or "")[:300]
        out.append(Candidate(a.get("username", ""), sources=["mastodon"], display_name=a.get("display_name"),
                             profile_url=a.get("url"), avatar=a.get("avatar"), bio=bio))
    return out


async def src_keybase(s, name):
    data = await _get_json(s, f"https://keybase.io/_/api/1.0/user/autocomplete.json?q={quote(name)}")
    out = []
    for c in (data or {}).get("completions", [])[:10]:
        comps = c.get("components", {})
        u = (comps.get("username") or {}).get("val")
        if u:
            out.append(Candidate(u, sources=["keybase"], display_name=(comps.get("full_name") or {}).get("val"),
                                 profile_url=f"https://keybase.io/{u}"))
    return out


async def src_hackernews(s, name):
    data = await _get_json(s, f"https://hn.algolia.com/api/v1/search?query={quote(name)}&tags=comment&hitsPerPage=20")
    seen, out = set(), []
    lc = name.lower()
    for h in (data or {}).get("hits", []):
        a = h.get("author")
        if a and a not in seen and lc in (h.get("comment_text") or "").lower():
            seen.add(a)
            out.append(Candidate(a, sources=["hackernews"], profile_url=f"https://news.ycombinator.com/user?id={a}"))
    return out[:5]


async def src_gravatar(s, email):
    h = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    data = await _get_json(s, f"https://api.gravatar.com/v3/profiles/{h}")
    if not data:
        data = await _get_json(s, f"https://en.gravatar.com/{hashlib.md5(email.strip().lower().encode()).hexdigest()}.json")
        data = ((data or {}).get("entry") or [None])[0]
    if not data:
        return []
    out = []
    u = data.get("preferred_username") or data.get("preferredUsername")
    if u:
        out.append(Candidate(u, sources=["gravatar"], display_name=data.get("display_name") or data.get("displayName"),
                             profile_url=data.get("profile_url") or data.get("profileUrl"),
                             avatar=data.get("avatar_url") or data.get("thumbnailUrl"),
                             bio=data.get("description") or data.get("aboutMe"), location=data.get("location"),
                             company=data.get("company")))
    for acc in data.get("verified_accounts") or data.get("accounts") or []:
        un = acc.get("username") or acc.get("shortname")
        if un and un != u:
            out.append(Candidate(un, sources=[f"gravatar:{acc.get('service_label') or acc.get('domain') or 'link'}"],
                                 profile_url=acc.get("url")))
    return out


NAME_SOURCES = [src_github, src_gitlab, src_bluesky, src_mastodon, src_keybase, src_hackernews]


def _context_score(c: Candidate, full_name: str, ctx: dict) -> float:
    score = 0.0
    fn = fold(full_name)
    dn = fold(c.display_name or "")
    if dn and dn == fn:
        score += 40
    elif dn and all(p in dn.split() for p in fn.split()):
        score += 30
    elif dn and fn.split()[0] in dn:
        score += 10
    blob = " ".join(filter(None, [c.bio, c.location, c.company, c.display_name])).lower()
    for key, w in (("location", 20), ("employer", 20), ("school", 15)):
        v = (ctx.get(key) or "").strip().lower()
        if v and v in blob:
            score += w
    for kw in ctx.get("keywords") or []:
        if kw and kw.lower() in blob:
            score += 8
    return score


async def find_candidates(kind: str, query: str, context: Optional[dict] = None,
                          max_candidates: int = 8, proxy: Optional[str] = None) -> List[dict]:
    context = context or {}
    merged: Dict[str, Candidate] = {}

    def merge(c: Candidate, base: float):
        key = c.username.lower()
        if not key or len(key) < 2:
            return
        cur = merged.get(key)
        if cur is None:
            c.score = base
            merged[key] = c
            return
        if set(c.sources) <= set(cur.sources):
            cur.score = max(cur.score, base)  # same source twice (e.g. mastodon instances)
        else:
            cur.score += base * 0.5 + 5
        cur.sources = sorted(set(cur.sources + c.sources))
        for f in ("display_name", "profile_url", "avatar", "bio", "location", "company"):
            if not getattr(cur, f) and getattr(c, f):
                setattr(cur, f, getattr(c, f))

    full_name = query
    async with aiohttp.ClientSession(headers={"User-Agent": UA, "Accept": "application/json"}) as s:
        if kind == "email":
            local = query.split("@")[0]
            merge(Candidate(fold(local).replace(" ", "") or local, sources=["email-local-part"]), 50)
            for c in await src_gravatar(s, query):
                merge(c, 70)
            full_name = next((c.display_name for c in merged.values() if c.display_name), "") or ""
            if context.get("name"):
                full_name = context["name"]
            if not full_name:
                return [c.public() for c in sorted(merged.values(), key=lambda c: -c.score)][:max_candidates]

        results = await asyncio.gather(*(src(s, full_name) for src in NAME_SOURCES), return_exceptions=True)
        for res in results:
            if isinstance(res, BaseException):
                log.debug("people source failed: %s", res)
                continue
            for c in res:
                merge(c, 20 + _context_score(c, full_name, context))

    for p in name_permutations(full_name):
        merge(Candidate(p["username"], sources=["permutation"]), 35 * p["weight"])

    ranked = sorted(merged.values(), key=lambda c: -c.score)
    return [c.public() for c in ranked[:max(max_candidates, 1) * 3]]
