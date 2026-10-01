"""Tests for false-positive scoring, name permutations and the FastAPI server."""

import os

import pytest

from maigret.scoring import Fingerprint, classify_page, redirected_away, score, similarity
from maigret.people import name_permutations, split_name, _context_score, Candidate


PROFILE = """<html><head><title>torvalds (Linus) - Site</title>
<meta property="og:url" content="https://site.example/torvalds"></head>
<body><h1>Linus Torvalds</h1><p>Joined 2011. 120 followers. Kernel hacker in Portland.</p>
<p>Repositories: linux, subsurface, test-tlb, libdc-for-dirk, uemacs, pesconvert</p></body></html>"""

GENERIC = """<html><head><title>Site - discover people</title></head>
<body><h1>Welcome</h1><p>Sign up today to connect with friends and share photos and videos
with people around the world. Explore trending topics now.</p></body></html>"""


def fp(html, status=200, url="https://site.example/x"):
    return Fingerprint.build(html, status, url)


def test_identical_control_page_is_demoted():
    t = fp(GENERIC.replace("Welcome", "Welcome"), url="https://site.example/torvalds")
    c = fp(GENERIC, url="https://site.example/qz81kd0a9xmd")
    v = score(check_type="status_code", has_presence_strs=False, has_absence_strs=False,
              username="torvalds", requested_url="https://site.example/torvalds",
              target=t, target_html=GENERIC, control=c, control_name="qz81kd0a9xmd", control_claimed=True)
    assert v.confidence < 30
    assert any("near-identical" in r for r in v.reasons)


def test_distinct_profile_scores_high():
    t = fp(PROFILE, url="https://site.example/torvalds")
    c = fp("<title>Not Found</title>nope", status=404)
    v = score(check_type="status_code", has_presence_strs=False, has_absence_strs=False,
              username="torvalds", requested_url="https://site.example/torvalds",
              target=t, target_html=PROFILE, control=c, control_name="qz81kd0a9xmd", control_claimed=False)
    assert v.status == "claimed"
    assert v.confidence >= 80


def test_echoed_username_does_not_break_similarity():
    tpl = "<title>{u} - profile not available</title><body>Sorry {u}, nothing here. " + "lorem ipsum " * 40 + "</body>"
    a = fp(tpl.format(u="torvalds"))
    b = fp(tpl.format(u="qz81kd0a9xmd"))
    assert similarity(a, b, "torvalds", "qz81kd0a9xmd") > 0.9


def test_not_found_title_is_rejected():
    html = "<title>User not found | Site</title><body>x</body>"
    assert classify_page(fp(html), html)
    v = score(check_type="status_code", has_presence_strs=False, has_absence_strs=False,
              username="bob", requested_url="https://s/bob", target=fp(html), target_html=html)
    assert v.status == "available"


def test_redirect_to_homepage_detected():
    assert redirected_away("https://s.example/u/torvalds", "https://s.example/", "torvalds")
    assert not redirected_away("https://s.example/u/torvalds", "https://s.example/u/torvalds/", "torvalds")


def test_feedback_lowers_confidence():
    t = fp(PROFILE, url="https://site.example/torvalds")
    base = score(check_type="message", has_presence_strs=True, has_absence_strs=True, username="torvalds",
                 requested_url="https://site.example/torvalds", target=t, target_html=PROFILE)
    bad = score(check_type="message", has_presence_strs=True, has_absence_strs=True, username="torvalds",
                requested_url="https://site.example/torvalds", target=t, target_html=PROFILE,
                fp_reports=4, reliability=0.1)
    assert bad.confidence < base.confidence


def test_name_permutations():
    names = [p["username"] for p in name_permutations("Robert James Smith")]
    assert names[0] == "robertsmith"
    for n in ("robert.smith", "rsmith", "bobsmith", "rjsmith"):
        assert n in names
    assert split_name("José  Álvarez-López") == ("jose", ["alvarez"], "lopez")


def test_context_score_prefers_matching_location():
    a = Candidate("lt1", display_name="Linus Torvalds", location="Portland, OR")
    b = Candidate("lt2", display_name="Linus Torvalds", location="Chengdu")
    ctx = {"location": "portland"}
    assert _context_score(a, "Linus Torvalds", ctx) > _context_score(b, "Linus Torvalds", ctx)


# --------------------------------------------------------------------------- #
# server
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MW_DATA_DIR", str(tmp_path))
    import importlib
    import maigret.server.app as appmod
    importlib.reload(appmod)
    appmod.DATA_DIR = str(tmp_path)
    appmod.DB_FILE = os.path.join(os.path.dirname(__file__), "db.json")
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        yield c, appmod


def test_server_basic_endpoints(client):
    c, _ = client
    assert c.get("/api/health").json()["ok"]
    assert c.get("/api/stats").json()["sites_total"] > 0
    assert c.get("/api/sites?limit=5").json()["total"] > 0
    assert isinstance(c.get("/api/tags").json(), list)
    s = c.put("/api/settings", json={"defaults": {"top_sites": 50, "min_confidence": 70}}).json()
    assert s["defaults"]["min_confidence"] == 70
    assert c.post("/api/searches", json={"kind": "email", "query": "nope"}).status_code == 422
    assert c.post("/api/searches", json={"kind": "username", "query": "a b"}).status_code == 422


def test_server_feedback_roundtrip(client):
    c, appmod = client
    st = appmod.S.store
    st.create_search({"id": "s1", "kind": "username", "query": "x", "context": {}, "options": {},
                      "status": "done", "created_at": "2026-01-01", "counts": {}})
    st.add_result({"id": "s1:GitHub:x", "search_id": "s1", "site": "GitHub", "username": "x",
                   "status": "claimed", "confidence": 90, "url": "https://github.com/x", "url_main": "https://github.com/",
                   "tags": [], "ids": {"fullname": "X Y"}, "reasons": [], "evidence": {}})
    assert c.post("/api/results/feedback", json={"result_id": "s1:GitHub:x", "verdict": "false_positive"}).json()["ok"]
    assert st.site_stats()["GitHub"]["fp_reports"] == 1
    c.post("/api/results/feedback", json={"result_id": "s1:GitHub:x", "verdict": "confirmed"})
    stats = st.site_stats()["GitHub"]
    assert stats["fp_reports"] == 0 and stats["confirmations"] == 1
    r = c.get("/api/searches/s1/export?format=csv")
    assert r.status_code == 200 and "GitHub" in r.text
    g = c.get("/api/searches/s1/graph").json()
    assert any(n["type"] == "account" for n in g["nodes"])


def test_server_auth(client, monkeypatch):
    c, appmod = client
    monkeypatch.setattr(appmod, "TOKEN", "sekrit")
    assert c.get("/api/stats").status_code == 401
    assert c.get("/api/stats", headers={"Authorization": "Bearer sekrit"}).status_code == 200
    assert c.get("/api/health").status_code == 200
