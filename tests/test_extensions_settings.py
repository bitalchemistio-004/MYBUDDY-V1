"""Extensions (S93): groups of PC abilities and single apps the user can switch off.

Settings live in a tmp file (MBUDDY_SETTINGS_PATH), like
tests/test_settings_sync.py, and the installed-app list is replaced, so no
test touches the real settings or reads the real Start menu.
"""
import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import setup_checks, ui_settings as s

TOPICS = ("apps", "web", "files", "settings", "system", "music", "calendar", "clipboard", "office", "brain",
          "create", "routines", "phone", "messages", "day", "habits", "cloud")
APP = {"Origin": "http://tauri.localhost"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    path = tmp_path / "app_settings.json"
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(path))
    s.reset_cache()
    monkeypatch.setattr(setup_checks, "ollama_models", lambda: ["qwen3:4b-instruct"])
    yield path
    s.reset_cache()


def run(coro_fn):
    async def go():
        from api.ui_api import origin_guard, register_ui_routes
        app = web.Application(middlewares=[origin_guard])
        register_ui_routes(app)
        async with TestClient(TestServer(app)) as client:
            return await coro_fn(client)
    return asyncio.run(go())


# ── the settings ─────────────────────────────────────────────────────────
def test_defaults_every_group_on_and_no_app_rules():
    d = s.all_settings()
    assert d["extensions"] == {topic: True for topic in TOPICS}
    assert s.EXTENSION_TOPICS == TOPICS and set(s.EXTENSION_INFO) == set(TOPICS)
    assert d["app_rules"] == {}


@pytest.mark.parametrize("change", [
    {"extensions": ["apps"]},
    {"extensions": {"lights": False}},
    {"extensions": {"power": False}},              # power has no switch of its own: it follows system
    {"extensions": {"discovery": False}},
    {"extensions": {"apps": "off"}},
    {"extensions": {"apps": 0}},
    {"extensions": {"apps": None}},
    {"extensions": {"files": False, "apps": "no"}},  # one bad value refuses the whole change
    {"app_rules": ["Steam"]},
    {"app_rules": {"Steam": "off"}},
    {"app_rules": {"Steam": 0}},
    {"app_rules": {"Steam": None}},
    {"app_rules": {"": False}},
    {"app_rules": {"   ": False}},
    {"app_rules": {7: False}},
    {"app_rules": {"x" * 61: False}},
    {"app_rules": {"Bad\x00name": False}},
    {"app_rules": {"Steam": False, "  steam ": True}},  # the same app twice
    {"app_rules": {f"App {i}": True for i in range(201)}},
])
def test_bad_keys_and_values_are_refused_and_nothing_is_stored(change, isolated):
    with pytest.raises(s.SettingsError):
        s.update(change)
    assert s.all_settings() == s.DEFAULTS
    assert not isolated.exists()


def test_the_limits_themselves_are_allowed():
    s.update({"app_rules": {"x" * 60: False}})
    s.update({"app_rules": {f"App {i}": True for i in range(200)}})
    assert len(s.get("app_rules")) == 200


def test_a_partial_extensions_update_merges_and_persists():
    s.update({"extensions": {"files": False}})
    s.update({"extensions": {"web": False}})
    s.reset_cache()  # read back from the file
    d = s.get("extensions")
    assert d["files"] is False and d["web"] is False
    assert all(d[topic] for topic in TOPICS if topic not in ("files", "web"))
    s.update({"extensions": {"files": True}})
    assert s.get("extensions")["files"] is True and s.get("extensions")["web"] is False


def test_app_rules_are_replaced_whole_and_names_tidied():
    s.update({"app_rules": {"  Visual   Studio Code ": False, "Spotify": True}})
    assert s.get("app_rules") == {"Visual Studio Code": False, "Spotify": True}
    s.update({"app_rules": {"Spotify": True}})  # removing an app: the list without it
    assert s.get("app_rules") == {"Spotify": True}


def test_saved_values_load_and_a_bad_one_falls_back_to_its_default(isolated):
    # An older file without the newest group: that group is on.
    isolated.write_text(json.dumps({"extensions": {"apps": False}, "app_rules": {"Steam": False}}), encoding="utf-8")
    s.reset_cache()
    assert s.get("extensions") == {**{topic: True for topic in TOPICS}, "apps": False}
    assert s.get("app_rules") == {"Steam": False}
    isolated.write_text(json.dumps({"extensions": {"apps": "nope"}, "app_rules": {"": True}}), encoding="utf-8")
    s.reset_cache()
    assert s.get("extensions") == s.DEFAULTS["extensions"] and s.get("app_rules") == {}


# ── the checks brain code calls ──────────────────────────────────────────
def test_extension_enabled_follows_the_switches():
    assert all(s.extension_enabled(topic) for topic in TOPICS)
    s.update({"extensions": {"files": False}})
    assert s.extension_enabled("files") is False and s.extension_enabled(" Files ") is False
    assert s.extension_enabled("apps") is True


def test_power_follows_system():
    assert s.extension_enabled("power") is True
    s.update({"extensions": {"system": False}})
    assert s.extension_enabled("power") is False and s.extension_enabled("system") is False
    s.update({"extensions": {"system": True}})
    assert s.extension_enabled("power") is True


@pytest.mark.parametrize("topic", ["discovery", "lights", "", None, 42])
def test_a_topic_without_a_switch_is_on(topic):
    s.update({"extensions": {t: False for t in TOPICS}})
    assert s.extension_enabled(topic) is True


def test_app_allowed_ignores_case_and_spaces():
    s.update({"app_rules": {"Steam": False, "Spotify": True}})
    assert s.app_allowed("Steam") is False
    assert s.app_allowed("  sTEAM ") is False
    assert s.app_allowed("Spotify") is True
    assert s.app_allowed("Discord") is True   # not in the list: allowed
    assert s.app_allowed("Steam VR") is True  # another app's name


@pytest.mark.parametrize("name", ["", "   ", None, 7])
def test_app_allowed_takes_odd_names_without_raising(name):
    s.update({"app_rules": {"Steam": False}})
    assert s.app_allowed(name) is True


def test_both_checks_never_raise_even_if_the_store_fails(monkeypatch):
    def broken(key):
        raise RuntimeError("the settings can't be read")
    monkeypatch.setattr(s, "get", broken)
    assert s.extension_enabled("files") is True
    assert s.app_allowed("Steam") is True


def test_every_pc_ability_topic_has_a_switch():
    # The switches follow the topics in brain/pc_actions.py REGISTRY: a new
    # topic needs a switch of its own (or an alias, like power -> system).
    from brain import pc_actions
    topics = {a.topic for a in pc_actions.REGISTRY.values()}
    assert all(s.extension_for(t) or t == "discovery" for t in topics), topics
    assert {s.extension_for(t) for t in topics} - {None} == set(TOPICS)  # every switch governs something
    assert s.extension_for("power") == "system" and s.extension_for("discovery") is None


# ── the API ──────────────────────────────────────────────────────────────
def test_the_settings_api_takes_the_new_keys_through_validation():
    async def go(c):
        r = await c.post("/ui/settings", json={"changes": {"extensions": {"music": False}, "app_rules": {"Steam": False}}}, headers=APP)
        body = await r.json()
        assert r.status == 200 and body["settings"]["extensions"]["music"] is False
        assert body["settings"]["app_rules"] == {"Steam": False}
        r = await c.post("/ui/settings", json={"changes": {"extensions": {"music": "off"}}}, headers=APP)
        assert r.status == 400 and "extensions" in (await r.json())["error"]
        r = await c.post("/ui/settings", json={"changes": {"app_rules": {"": False}}}, headers=APP)
        assert r.status == 400 and s.get("app_rules") == {"Steam": False}
        opts = (await (await c.get("/ui/settings")).json())["options"]
        assert [g["id"] for g in opts["extensions"]] == list(TOPICS)
        assert all(g["title"] and g["description"] for g in opts["extensions"])
        assert opts["app_rules_max"] == 200 and opts["app_name_max"] == 60
    run(go)


# Like the real index: App Paths' "notepad" comes before the Start menu's "Notepad".
FAKE_APPS = {"Spotify": "a.lnk", "Steam": "b.lnk", "notepad": r"C:\Windows\notepad.exe", "Notepad": "shell:AppsFolder\\n",
             "Windows Speech Recognition": "d.lnk", "Microsoft Store": "shell:AppsFolder\\x", "Visual Studio Code": "e.lnk"}


def test_apps_are_filtered_by_name_in_any_case(monkeypatch):
    from brain import pc_actions
    built = []
    monkeypatch.setattr(pc_actions, "app_index", lambda: built.append(1) or dict(FAKE_APPS))

    async def go(c):
        body = await (await c.get("/ui/apps?q=SP")).json()
        assert body["ok"] and body["apps"] == ["Spotify", "Windows Speech Recognition"]  # starts-with first
        body = await (await c.get("/ui/apps?q=notepad")).json()
        assert body["apps"] == ["Notepad"] and body["total"] == 1  # once, with its capitals
        body = await (await c.get("/ui/apps?q=zzz")).json()
        assert body["ok"] and body["apps"] == [] and body["installed"] == len(FAKE_APPS)
    run(go)
    assert len(built) == 3  # the index is built once per request


def test_apps_are_capped_at_50(monkeypatch):
    from brain import pc_actions
    monkeypatch.setattr(pc_actions, "app_index", lambda: {f"Game {i:03d}": "x.lnk" for i in range(80)})

    async def go(c):
        body = await (await c.get("/ui/apps?q=game")).json()
        assert len(body["apps"]) == 50 and body["total"] == 80 and body["apps"][0] == "Game 000"
    run(go)


def test_apps_says_so_when_the_list_cannot_be_read(monkeypatch):
    from brain import pc_actions
    monkeypatch.setattr(pc_actions, "app_index", lambda: {})

    async def go(c):
        r = await c.get("/ui/apps?q=steam")
        assert r.status == 500 and (await r.json())["ok"] is False
    run(go)


def test_apps_is_local_only(monkeypatch):
    from api.ui_api import apps_handler
    monkeypatch.delenv("MBUDDY_DASHBOARD_ALLOW_LAN", raising=False)

    class LanRequest:
        query = {"q": "steam"}
        remote = "192.168.1.50"

    with pytest.raises(web.HTTPForbidden):
        asyncio.run(apps_handler(LanRequest()))


def test_abilities_are_grouped_by_topic_with_their_switch():
    from brain import pc_actions

    async def go(c):
        body = await (await c.get("/ui/abilities")).json()
        topics = {t["topic"]: t for t in body["topics"]}
        assert list(topics) == list(TOPICS)
        names = {topic: {a["name"] for a in group["abilities"]} for topic, group in topics.items()}
        assert {"find_apps", "open_app"} <= names["apps"]
        assert {"set_brightness", "lock_screen", "power"} <= names["system"]  # power is in system
        grouped = sorted(n for group in names.values() for n in group)
        assert grouped == sorted(n for n, a in pc_actions.REGISTRY.items() if a.topic != "discovery")  # each once
        assert all(g["enabled"] and g["title"] and g["description"] for g in topics.values())
        assert topics["system"]["state"] == "asks first" and topics["apps"]["state"] == "on"
        assert body["abilities"][-1]["state"] == "not yet"
        await c.post("/ui/settings", json={"changes": {"extensions": {"files": False}}}, headers=APP)
        topics = {t["topic"]: t for t in (await (await c.get("/ui/abilities")).json())["topics"]}
        assert topics["files"]["enabled"] is False and topics["files"]["state"] == "off"
        assert topics["files"]["blocked_by"] is None and topics["apps"]["state"] == "on"
    run(go)


def test_a_group_left_on_is_reported_off_when_another_switch_blocks_it():
    # Today every PC ability also needs a tool permission (brain/agent.py
    # TOOL_SETTING: "windows"); a group says which one keeps it off.
    from brain import agent
    gate = agent.TOOL_SETTING.get("open_app")
    assert gate in s.TOOL_NAMES

    async def go(c):
        await c.post("/ui/settings", json={"changes": {"tool_permissions": {gate: "off"}}}, headers=APP)
        topics = (await (await c.get("/ui/abilities")).json())["topics"]
        gated = [t for t in topics if all(agent.TOOL_SETTING.get(a["name"]) == gate for a in t["abilities"])]
        assert gated and any(t["topic"] == "apps" for t in gated)
        assert all(t["enabled"] and t["state"] == "off" and t["blocked_by"] == gate for t in gated)
    run(go)
