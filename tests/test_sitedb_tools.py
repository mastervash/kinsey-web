"""Unit tests for tools/ (site database maintenance). Pure: no network."""

import copy

import pytest

from maigret.sites import MaigretDatabase, MaigretSite
from tools import import_wmn as wmn
from tools import sitedb_common as common
from tools import tranco_rank as tranco
from tools import verify_sites as verify


def _entry(**kw):
    base = {
        "name": "Example",
        "uri_check": "https://example.com/u/{account}",
        "e_code": 200,
        "e_string": "profile-header",
        "m_code": 404,
        "m_string": "User not found",
        "known": ["alice", "bob"],
        "cat": "social",
    }
    base.update(kw)
    return base


ENGINES = {
    "Mastodon": {
        "site": {
            "checkType": "status_code",
            "url": "{urlMain}/@{username}",
            "urlProbe": "{urlMain}/api/v1/accounts/lookup?acct={username}",
        }
    },
    "XenForo": {"site": {"checkType": "message", "absenceStrs": ["not found"]}},
}


# --- convert_wmn_entry ---------------------------------------------------


def test_convert_basic_message_check():
    name, site, reason = wmn.convert_wmn_entry(_entry())
    assert reason == ""
    assert name == "Example"
    assert site == {
        "url": "https://example.com/u/{username}",
        "urlMain": "https://example.com/",
        "checkType": "message",
        "presenseStrs": ["profile-header"],
        "absenceStrs": ["User not found"],
        "usernameClaimed": "alice",
        "usernameUnclaimed": "noonewouldeverusethis7",
        "tags": ["social"],
        "origin": "wmn",
    }


def test_convert_uses_uri_pretty_and_probe():
    _, site, _ = wmn.convert_wmn_entry(
        _entry(
            uri_check="https://api.example.com/v1/users/{account}",
            uri_pretty="https://example.com/{account}",
        )
    )
    assert site["url"] == "https://example.com/{username}"
    assert site["urlProbe"] == "https://api.example.com/v1/users/{username}"
    assert site["urlMain"] == "https://example.com/"


def test_convert_no_probe_when_same_as_url():
    _, site, _ = wmn.convert_wmn_entry(_entry(uri_pretty="https://example.com/u/{account}"))
    assert "urlProbe" not in site


def test_convert_empty_m_string_has_no_absence():
    _, site, _ = wmn.convert_wmn_entry(_entry(m_string=""))
    assert "absenceStrs" not in site
    assert site["presenseStrs"] == ["profile-header"]


def test_convert_nsfw_category():
    _, site, _ = wmn.convert_wmn_entry(_entry(cat="xx NSFW xx"))
    assert site["tags"] == ["nsfw"]
    assert site["nsfw"] is True


def test_convert_misc_category_adds_no_tag():
    _, site, _ = wmn.convert_wmn_entry(_entry(cat="misc"))
    assert "tags" not in site
    assert "nsfw" not in site


def test_convert_headers_kept():
    _, site, _ = wmn.convert_wmn_entry(_entry(headers={"Accept": "application/json"}))
    assert site["headers"] == {"Accept": "application/json"}


@pytest.mark.parametrize(
    "override,reason_part",
    [
        ({"valid": False}, "invalid"),
        ({"known": []}, "known"),
        ({"uri_check": "https://example.com/static"}, "{account}"),
        ({"e_string": "", "m_string": ""}, "e_string"),
        ({"e_code": 403}, "error status"),
        ({"e_code": 301}, "redirect"),
        ({"m_code": 500, "m_string": ""}, "error status"),
        ({"uri_check": "https://example.com/{account}?x={foo}"}, "braces"),
    ],
)
def test_convert_skips(override, reason_part):
    _, site, reason = wmn.convert_wmn_entry(_entry(**override))
    assert site is None
    assert reason_part in reason


def test_convert_post_json_body():
    _, site, reason = wmn.convert_wmn_entry(
        _entry(
            uri_check="https://example.com/api/check",
            uri_pretty="https://example.com/{account}",
            post_body='{"username":"{account}","limit":10}',
            headers={"Content-Type": "application/json"},
        )
    )
    assert reason == ""
    assert site["requestMethod"] == "POST"
    assert site["requestPayload"] == {"username": "{username}", "limit": 10}
    assert site["urlProbe"] == "https://example.com/api/check"


def test_convert_post_form_body_normalizes_content_type():
    _, site, _ = wmn.convert_wmn_entry(
        _entry(
            uri_check="https://example.com/ajax",
            uri_pretty="https://example.com/{account}",
            post_body="action=check&username={account}",
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
    )
    assert site["requestPayload"] == {"action": "check", "username": "{username}"}
    # maigret only form-encodes on this exact header value
    assert site["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


@pytest.mark.parametrize(
    "body",
    [
        '{"query":"query{User(name:\\"{account}\\"){id}}"}',  # GraphQL braces
        '{"variables":{"username":"{account}"}}',  # nested
        "not json",
    ],
)
def test_convert_post_unsupported_bodies(body):
    _, site, reason = wmn.convert_wmn_entry(
        _entry(
            uri_check="https://example.com/graphql",
            post_body=body,
            headers={"Content-Type": "application/json"},
        )
    )
    assert site is None
    assert reason.startswith("post_body")


def test_convert_strip_bad_char_regex():
    _, site, _ = wmn.convert_wmn_entry(_entry(strip_bad_char="."))
    assert site["regexCheck"] == "^[^\\.]+$"


def test_converted_site_loads_in_maigret():
    _, site, _ = wmn.convert_wmn_entry(_entry(cat="xx NSFW xx"))
    s = MaigretSite("Example", site)
    assert s.check_type == "message"
    assert s.absence_strs == ["User not found"]
    # provenance must not hijack the mirror field
    assert s.source is None
    assert s.pretty_name == "Example"


# --- domain helpers ------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.Example.com/u/{username}", "example.com"),
        ("https://m.example.co.uk/", "example.co.uk"),
        ("https://{username}.tumblr.com/", "tumblr.com"),
        ("http://user:pw@forum.example.org:8080/x", "forum.example.org"),
        ("", ""),
    ],
)
def test_normalize_domain(url, expected):
    assert common.normalize_domain(url) == expected


def test_is_weak_site():
    assert common.is_weak_site({"disabled": True, "checkType": "message"}, ENGINES)
    assert common.is_weak_site({"checkType": "status_code"}, ENGINES)
    assert not common.is_weak_site({"checkType": "status_code", "absenceStrs": ["x"]}, ENGINES)
    assert not common.is_weak_site({"checkType": "message"}, ENGINES)
    assert not common.is_weak_site({"engine": "XenForo"}, ENGINES)
    # engine with an API probe is a real check
    assert not common.is_weak_site({"engine": "Mastodon"}, ENGINES)


# --- import_wmn dedupe / merge ------------------------------------------


def _db():
    return {
        "sites": {
            "Strong": {
                "url": "https://strong.com/{username}",
                "urlMain": "https://strong.com/",
                "checkType": "message",
                "absenceStrs": ["nope"],
                "usernameClaimed": "a",
                "usernameUnclaimed": "b",
                "tags": ["forum"],
            },
            "Weak": {
                "url": "https://www.weak.com/{username}",
                "urlMain": "https://www.weak.com/",
                "checkType": "status_code",
                "alexaRank": 42,
                "usernameClaimed": "a",
                "usernameUnclaimed": "b",
                "tags": ["photo"],
                "headers": {"X-Old": "1"},
            },
            "Off": {
                "url": "https://off.com/{username}",
                "urlMain": "https://off.com/",
                "checkType": "message",
                "absenceStrs": ["x"],
                "disabled": True,
                "usernameClaimed": "a",
                "usernameUnclaimed": "b",
            },
        },
        "engines": copy.deepcopy(ENGINES),
        "tags": ["forum", "photo", "social"],
    }


def _feed(*entries):
    return {"sites": list(entries)}


def test_import_adds_skips_and_merges():
    db = _db()
    stats = wmn.import_wmn(
        db,
        _feed(
            _entry(name="New", uri_check="https://new.com/{account}"),
            _entry(name="strong", uri_check="https://strong.com/{account}"),
            _entry(name="weak", uri_check="https://weak.com/{account}", cat="xx NSFW xx"),
            _entry(name="off", uri_check="https://off.com/user/{account}"),
        ),
    )
    assert stats["added"] == ["New"]
    assert [m["name"] for m in stats["merged"]] == ["Weak", "Off"]
    assert stats["skipped_existing"][0]["name"] == "strong"

    s = db["sites"]
    assert s["New"]["origin"] == "wmn"
    assert s["Strong"] == _db()["sites"]["Strong"]

    weak = s["Weak"]
    assert weak["origin"] == "wmn-merged"
    assert weak["checkType"] == "message"
    assert weak["absenceStrs"] == ["User not found"]
    assert weak["alexaRank"] == 42  # metadata kept
    assert weak["tags"] == ["photo", "nsfw"]  # union
    assert weak["nsfw"] is True
    assert "headers" not in weak  # stale detection field dropped
    assert weak["usernameClaimed"] == "alice"

    off = s["Off"]
    assert "disabled" not in off
    assert off["url"] == "https://off.com/user/{username}"
    assert "nsfw" in db["tags"]


def test_import_name_collision_gets_suffix():
    db = _db()
    stats = wmn.import_wmn(db, _feed(_entry(name="Strong", uri_check="https://other.net/{account}")))
    # same name, unrelated domain: Strong is a real check, so it is "covered"
    # via the name fallback rather than duplicated
    assert stats["added"] == []
    db = _db()
    db["sites"]["Strong"]["origin"] = "wmn"
    stats = wmn.import_wmn(db, _feed(_entry(name="Strong", uri_check="https://other.net/{account}")))
    assert stats["added"] == ["Strong (WMN)"]


def test_import_is_idempotent():
    db = _db()
    feed = _feed(
        _entry(name="New", uri_check="https://new.com/{account}"),
        _entry(name="weak", uri_check="https://weak.com/{account}"),
    )
    wmn.import_wmn(db, feed)
    snapshot = copy.deepcopy(db)
    stats = wmn.import_wmn(db, feed)
    assert stats["added"] == [] and stats["merged"] == []
    assert sorted(stats["refreshed"]) == ["New", "Weak"]
    assert db == snapshot


def test_import_respects_rejects():
    db = _db()
    stats = wmn.import_wmn(db, _feed(_entry(name="weak", uri_check="https://weak.com/{account}")),
                           rejects={"Weak"})
    assert stats["merged"] == []
    assert db["sites"]["Weak"]["checkType"] == "status_code"


def test_imported_db_loads():
    db = _db()
    wmn.import_wmn(db, _feed(_entry(name="New", uri_check="https://new.com/{account}"),
                             _entry(name="weak", uri_check="https://weak.com/{account}")))
    mdb = MaigretDatabase().load_from_json(db)
    assert {s.name for s in mdb.sites} == {"Strong", "Weak", "Off", "New"}


@pytest.mark.parametrize(
    "old,new,keep",
    [
        ("ok", "ok", True),
        ("error", "ok", True),
        ("claimed_not_found", "ok", True),
        ("ok", "claimed_not_found", False),
        ("ok", "error", False),
        ("error", "error", True),
        ("false_positive", "claimed_not_found", False),  # never keep a failing check
        ("dead", "dead", False),
    ],
)
def test_should_keep_merge(old, new, keep):
    assert wmn.should_keep_merge(old, new) is keep


# --- tranco --------------------------------------------------------------


def test_tranco_parse_and_lookup():
    ranks = tranco.parse_tranco(b"1,google.com\n2,example.co.uk\nbad,row\n3,tumblr.com\n")
    assert ranks == {"google.com": 1, "example.co.uk": 2, "tumblr.com": 3}
    assert tranco.lookup_rank("forum.example.co.uk", ranks) == 2
    assert tranco.lookup_rank("nowhere.org", ranks) is None


def test_tranco_apply():
    db = {"sites": {
        "A": {"urlMain": "https://www.google.com/"},
        "B": {"url": "https://{username}.tumblr.com/"},
        "C": {"urlMain": "https://nowhere.org", "trancoRank": 5},
    }}
    stats = tranco.apply_ranks(db, {"google.com": 1, "tumblr.com": 3})
    assert db["sites"]["A"]["trancoRank"] == 1
    assert db["sites"]["B"]["trancoRank"] == 3
    assert "trancoRank" not in db["sites"]["C"]
    assert stats == {"ranked": 2, "unranked": 1, "cleared": 1}


# --- verify_sites (pure parts) ------------------------------------------


def _p(status, err=None):
    return {"status": status, "error_type": err}


@pytest.mark.parametrize(
    "probes,verdict",
    [
        ({"claimed": _p("CLAIMED"), "unclaimed": _p("AVAILABLE"), "random": _p("AVAILABLE")}, "ok"),
        ({"claimed": _p("AVAILABLE"), "unclaimed": _p("AVAILABLE"), "random": _p("AVAILABLE")},
         "claimed_not_found"),
        ({"claimed": _p("CLAIMED"), "unclaimed": _p("AVAILABLE"), "random": _p("CLAIMED")},
         "false_positive"),
        ({"claimed": _p("UNKNOWN", "Connecting failure (DNS)"),
          "unclaimed": _p("UNKNOWN", "Connecting failure"),
          "random": _p("UNKNOWN", "Connecting failure (DNS)")}, "dead"),
        ({"claimed": _p("UNKNOWN", "Bot protection"), "unclaimed": _p("UNKNOWN", "Bot protection"),
          "random": _p("UNKNOWN", "Bot protection")}, "error"),
        ({"claimed": _p("CLAIMED"), "unclaimed": _p("UNKNOWN", "Rate limited"),
          "random": _p("AVAILABLE")}, "ok"),
    ],
)
def test_classify(probes, verdict):
    assert verify.classify(probes) == verdict


def test_random_username_shape():
    u = verify.random_username()
    assert len(u) == 12
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789" for c in u)


def test_apply_results():
    data = {"sites": {
        "Good": {"quarantined": True, "quarantineReason": "dead"},
        "Bad": {},
        "Flaky": {},
    }}
    results = [
        {"name": "Good", "verdict": "ok"},
        {"name": "Bad", "verdict": "false_positive"},
        {"name": "Flaky", "verdict": "error"},
        {"name": "Missing", "verdict": "ok"},
    ]
    counts = verify.apply_results(data, results, "2026-10-01")
    assert data["sites"]["Good"] == {"lastVerified": "2026-10-01"}
    assert data["sites"]["Bad"] == {
        "lastVerified": "2026-10-01",
        "quarantined": True,
        "quarantineReason": "false_positive",
    }
    assert data["sites"]["Flaky"] == {"lastVerified": "2026-10-01"}
    assert "disabled" not in data["sites"]["Bad"]
    assert counts == {"quarantined": 1, "cleared": 1, "verified": 3}

    verify.apply_results(data, [{"name": "Flaky", "verdict": "error"}], "2026-10-02",
                         quarantine_errors=True)
    assert data["sites"]["Flaky"]["quarantineReason"] == "error"


def test_select_sites_filters_and_orders():
    db = MaigretDatabase().load_from_json({
        "sites": {
            "Low": {"url": "https://low.com/{username}", "urlMain": "https://low.com/",
                    "checkType": "status_code", "usernameClaimed": "a", "tags": ["photo"],
                    "trancoRank": 5000},
            "High": {"url": "https://high.com/{username}", "urlMain": "https://high.com/",
                     "checkType": "status_code", "usernameClaimed": "a", "tags": ["social"],
                     "trancoRank": 10},
            "Off": {"url": "https://off.com/{username}", "urlMain": "https://off.com/",
                    "checkType": "status_code", "usernameClaimed": "a", "disabled": True},
            "Onion": {"url": "http://x.onion/{username}", "urlMain": "http://x.onion/",
                      "checkType": "status_code", "usernameClaimed": "a", "protocol": "tor"},
        },
        "engines": {},
        "tags": [],
    })
    assert [s.name for s in verify.select_sites(db)] == ["High", "Low"]
    assert [s.name for s in verify.select_sites(db, limit=1)] == ["High"]
    assert [s.name for s in verify.select_sites(db, tags=["photo"])] == ["Low"]
    assert [s.name for s in verify.select_sites(db, names=["low"])] == ["Low"]
