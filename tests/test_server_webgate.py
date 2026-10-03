from maigret.server.jobs import webgate_config, webgate_settings


def test_disabled_without_url(monkeypatch):
    monkeypatch.delenv("KW_WEBGATE_URL", raising=False)
    assert webgate_config({}) is None
    assert webgate_config({"webgate": {"enabled": True, "url": ""}}) is None


def test_enabled_config():
    cfg = webgate_config({"webgate": {"enabled": True, "url": "http://x:8191/v1", "max_timeout_ms": 1}})
    assert cfg["modules"][0]["url"] == "http://x:8191/v1"
    assert cfg["modules"][0]["method"] == "json_api"
    assert cfg["modules"][0]["max_timeout_ms"] == 5000
    assert "cf_js_challenge" in cfg["trigger_protection"]


def test_env_preset(monkeypatch):
    monkeypatch.setenv("KW_WEBGATE_URL", "http://e:8191/v1")
    assert webgate_settings({})["enabled"] is True
    assert webgate_settings({"webgate": {"enabled": False}})["enabled"] is False
