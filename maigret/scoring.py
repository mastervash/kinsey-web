"""False-positive scoring.

A hit from the legacy checkType logic is only a *candidate*. This module turns
it into a scored verdict by comparing the target response with a control
response for a username that certainly does not exist on the same site.

If the site answers the control the same way it answered the target, the hit
carries no information: the site returns that page for every input.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import random
import re
import string
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

# --------------------------------------------------------------------------- #
# Fingerprints
# --------------------------------------------------------------------------- #

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w{3,}", re.U)
_META_RE = re.compile(
    r'<(?:meta|link)[^>]+(?:property|name|rel)=["\'](og:url|og:title|canonical|profile:username|twitter:title)["\'][^>]*>',
    re.I,
)
_CONTENT_RE = re.compile(r'(?:content|href)=["\']([^"\']*)["\']', re.I)


def control_username(seed_len: int = 12) -> str:
    """A username that certainly does not exist, shaped like a real one."""
    alphabet = string.ascii_lowercase + string.digits
    first = random.choice(string.ascii_lowercase)
    return first + "".join(random.choice(alphabet) for _ in range(seed_len - 1))


def visible_text(html: str) -> str:
    text = _SCRIPT_RE.sub(" ", html or "")
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def title_of(html: str) -> str:
    m = _TITLE_RE.search(html or "")
    if not m:
        return ""
    return _WS_RE.sub(" ", html_lib.unescape(m.group(1))).strip()[:300]


def meta_values(html: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _META_RE.finditer(html or ""):
        c = _CONTENT_RE.search(m.group(0))
        if c:
            out[m.group(1).lower()] = html_lib.unescape(c.group(1))
    return out


def simhash(text: str, bits: int = 64) -> int:
    """Charikar simhash over word 3-shingles. Stable across runs."""
    words = _WORD_RE.findall(text.lower())
    if not words:
        return 0
    shingles = [" ".join(words[i : i + 3]) for i in range(max(1, len(words) - 2))]
    v = [0] * bits
    for sh in shingles:
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= 1 << i
    return out


def simhash_similarity(a: int, b: int, bits: int = 64) -> float:
    if a == 0 and b == 0:
        return 1.0
    return 1.0 - bin(a ^ b).count("1") / bits


@dataclass
class Fingerprint:
    status: Optional[int]
    length: int
    title: str
    final_url: str
    simhash: int = 0
    meta: Dict[str, str] = field(default_factory=dict)
    text_sample: str = ""

    @classmethod
    def build(cls, html: str, status: Optional[int], url: str = "") -> "Fingerprint":
        text = visible_text(html or "")
        return cls(
            status=status,
            length=len(text),
            title=title_of(html),
            final_url=url or "",
            simhash=simhash(text),
            meta=meta_values(html),
            text_sample=text[:400],
        )

    def public(self) -> dict:
        d = asdict(self)
        d.pop("simhash", None)
        d.pop("text_sample", None)
        return d


def _strip_name(s: str, name: str) -> str:
    """Remove the username so echoed input does not make pages look different."""
    if not s or not name:
        return s or ""
    return re.sub(re.escape(name), "", s, flags=re.I)


def similarity(
    target: Fingerprint, control: Fingerprint, target_name: str, control_name: str
) -> float:
    """0..1, how alike two responses are once the username itself is removed."""
    if target.status != control.status:
        return 0.0
    t_title = _strip_name(target.title, target_name)
    c_title = _strip_name(control.title, control_name)
    title_same = 1.0 if t_title == c_title else 0.0

    lt, lc = target.length, control.length
    len_ratio = 1.0 if max(lt, lc) == 0 else min(lt, lc) / max(lt, lc)
    # Length differs by the username delta when the page echoes input.
    if abs(lt - lc) <= abs(len(target_name) - len(control_name)) * 4 + 8:
        len_ratio = 1.0

    sh = simhash_similarity(target.simhash, control.simhash)
    return round(0.35 * title_same + 0.25 * len_ratio + 0.40 * sh, 3)


# --------------------------------------------------------------------------- #
# Soft-404 / wall detection on the target page itself
# --------------------------------------------------------------------------- #

NOT_FOUND_PHRASES = [
    # en
    "page not found", "user not found", "profile not found", "account not found",
    "no such user", "this account doesn't exist", "this account does not exist",
    "the page you requested", "sorry, this page isn't available",
    "this page could not be found", "doesn't exist", "does not exist",
    "could not be found", "no results found", "nothing found", "has been suspended",
    "account suspended", "this user has been banned", "deactivated",
    "404 not found", "error 404", "member not found", "the specified member cannot be found",
    "user does not exist", "invalid user", "no user with",
    # ru
    "пользователь не найден", "страница не найдена", "не найден", "не существует",
    # de / fr / es / pt / it
    "seite nicht gefunden", "benutzer nicht gefunden", "page introuvable",
    "utilisateur introuvable", "página no encontrada", "usuario no encontrado",
    "página não encontrada", "usuário não encontrado", "pagina non trovata",
    "utente non trovato",
    # zh / ja
    "页面不存在", "用户不存在", "ページが見つかりません", "ユーザーが見つかりません",
]

LOGIN_WALL_PHRASES = [
    "log in to continue", "login to continue", "sign in to continue",
    "you must be logged in", "please log in", "please sign in", "login required",
    "members only", "you need to log in",
]

PARKED_PHRASES = [
    "this domain is for sale", "buy this domain", "domain is parked",
    "parked free", "sedoparking", "hugedomains", "dan.com", "afternic",
    "the domain name", "is for sale",
]

SEARCH_PAGE_PHRASES = ["search results for", "results for “", "showing results for"]


def _phrase_hits(text_lc: str, phrases: List[str]) -> List[str]:
    return [p for p in phrases if p in text_lc]


def classify_page(fp: Fingerprint, html: str) -> Optional[str]:
    """Return a reason string if the target page is clearly not a profile."""
    head = (fp.title + " " + fp.text_sample).lower()
    if _phrase_hits(head, PARKED_PHRASES[:4]):
        return "parked domain"
    hits = _phrase_hits(fp.title.lower(), NOT_FOUND_PHRASES)
    if hits:
        return f"not-found phrase in title: {hits[0]!r}"
    if fp.length < 2500:
        hits = _phrase_hits(fp.text_sample.lower(), NOT_FOUND_PHRASES)
        if hits:
            return f"not-found phrase on short page: {hits[0]!r}"
        if _phrase_hits(fp.text_sample.lower(), LOGIN_WALL_PHRASES):
            return "login wall"
    return None


def _path_of(url: str) -> str:
    try:
        return unquote(urlparse(url).path or "/").rstrip("/") or "/"
    except Exception:
        return url


def redirected_away(requested_url: str, final_url: str, username: str) -> bool:
    """Final URL lost the username (homepage, /login, /search ...)."""
    if not final_url or not requested_url:
        return False
    if username.lower() in unquote(requested_url).lower() and username.lower() not in unquote(final_url).lower():
        return True
    return _path_of(final_url) in ("/", "/login", "/signin", "/search", "/404", "/error")


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


@dataclass
class Verdict:
    status: str  # claimed | uncertain | available | blocked | unknown
    confidence: int
    reasons: List[str]
    similarity: Optional[float] = None


CHECK_TYPE_PRIOR = {"message": 60, "response_url": 55, "status_code": 40}


def score(
    *,
    check_type: str,
    has_presence_strs: bool,
    has_absence_strs: bool,
    username: str,
    requested_url: str,
    target: Fingerprint,
    target_html: str,
    control: Optional[Fingerprint] = None,
    control_name: str = "",
    control_claimed: Optional[bool] = None,
    ids: Optional[dict] = None,
    reliability: Optional[float] = None,
    fp_reports: int = 0,
    confirmations: int = 0,
) -> Verdict:
    reasons: List[str] = []
    conf = CHECK_TYPE_PRIOR.get(check_type, 45)
    reasons.append(f"base {conf} ({check_type})")

    if check_type == "message":
        if has_presence_strs and has_absence_strs:
            conf += 10
            reasons.append("+10 site has presence and absence markers")
        elif not has_presence_strs:
            conf -= 10
            reasons.append("-10 no presence markers")
    if check_type == "status_code" and not has_absence_strs:
        reasons.append("status-code only check, relying on control probe")

    wall = classify_page(target, target_html)
    if wall:
        return Verdict("available" if "not-found" in wall else "blocked", 5, reasons + [wall])

    if redirected_away(requested_url, target.final_url, username):
        conf -= 30
        reasons.append("-30 redirected away from profile URL")

    sim = None
    if control is not None:
        if control_claimed is False:
            conf += 25
            reasons.append("+25 control username rejected by same check")
        sim = similarity(target, control, username, control_name)
        if sim >= 0.92:
            conf -= 55
            reasons.append(f"-55 control page near-identical (sim {sim:.2f})")
        elif sim >= 0.8:
            conf -= 25
            reasons.append(f"-25 control page similar (sim {sim:.2f})")
        elif sim <= 0.5:
            conf += 15
            reasons.append(f"+15 control page differs (sim {sim:.2f})")
        else:
            conf += 5
            reasons.append(f"+5 control page partly differs (sim {sim:.2f})")
        if control_claimed and sim >= 0.8:
            conf -= 10
            reasons.append("-10 control username also matched check")
    else:
        conf -= 5
        reasons.append("-5 no control probe")

    uname = username.lower()
    meta_blob = " ".join(target.meta.values()).lower()
    if uname and (uname in target.title.lower() or uname in meta_blob):
        conf += 10
        reasons.append("+10 username in title/og metadata")
    elif uname and uname not in target_html.lower():
        conf -= 15
        reasons.append("-15 username absent from page")

    if ids:
        useful = [k for k in ids if not k.startswith("_")]
        if useful:
            bonus = min(15, 5 * len(useful))
            conf += bonus
            reasons.append(f"+{bonus} extracted profile fields ({', '.join(useful[:4])})")

    if reliability is not None:
        adj = int(round((reliability - 0.5) * 20))
        if adj:
            conf += adj
            reasons.append(f"{adj:+d} site reliability {reliability:.2f}")
    if fp_reports or confirmations:
        adj = max(-20, min(10, 3 * confirmations - 5 * fp_reports))
        if adj:
            conf += adj
            reasons.append(f"{adj:+d} user feedback ({confirmations} confirmed, {fp_reports} FP)")

    conf = max(0, min(100, conf))
    return Verdict("claimed", conf, reasons, sim)
