import sqlite3
import time

import pytest

from core import notif_watch as nw
from core import ui_settings


def make_db(path, rows):
    """A database shaped like Windows' own (only the columns that are read)."""
    con = sqlite3.connect(path)
    con.execute("create table NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT)")
    con.execute("create table Notification (\"Order\" INTEGER PRIMARY KEY, Id INTEGER, HandlerId INTEGER, Type TEXT, Payload BLOB, ArrivalTime INT64)")
    handlers = {}
    for i, (nid, app_id, title, text, arrived) in enumerate(rows, 1):
        hid = handlers.setdefault(app_id, len(handlers) + 1)
        con.execute("insert or ignore into NotificationHandler values (?, ?)", (hid, app_id))
        xml = f"<toast><visual><binding template='ToastGeneric'><text>{title}</text><text>{text}</text></binding></visual></toast>"
        con.execute("insert into Notification values (?, ?, ?, 'toast', ?, ?)", (i, nid, hid, xml.encode(), int((arrived + 11644473600) * 1e7)))
    con.commit()
    con.close()


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MBUDDY_NOTIFY_PATH", str(tmp_path / "notifications.json"))
    monkeypatch.setenv("MBUDDY_WPN_DB", str(tmp_path / "wpn.db"))
    ui_settings.reset_cache()
    nw._focus.clear()
    nw._apps_seen.clear()
    yield tmp_path
    ui_settings.reset_cache()


WA = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"


def test_the_windows_database_is_read_into_apps_senders_and_times(world):
    now = time.time()
    make_db(world / "wpn.db", [(1, WA, "Sara", "are you coming tonight?", now - 60), (2, "Claude_x!Claude", "Claude", "Done", now - 30)])
    got = nw.read_database()
    assert [(t["app"], t["title"], t["text"]) for t in got] == [("WhatsApp", "Sara", "are you coming tonight?"), ("Claude", "Claude", "Done")]
    assert abs(got[0]["arrived"] - (now - 60)) < 2


def test_only_the_apps_the_user_chose_are_kept_the_rest_are_only_counted(world):
    now = time.time()
    ui_settings.update({"notify": {"enabled": True, "apps": ["WhatsApp"], "model_judge": False}})
    make_db(world / "wpn.db", [(1, WA, "Sara", "call me?", now - 5), (2, "Claude_x!Claude", "Claude", "secret text", now - 5)])
    nw.process(now)
    kept = nw.recent()
    assert [r["app"] for r in kept] == ["WhatsApp"] and "secret" not in str(kept)
    assert nw.apps_seen() == {"WhatsApp": 1, "Claude": 1}          # Claude: only the name and a count


def test_a_waiting_message_is_reminded_after_the_time_and_a_chatty_one_is_left_alone(world):
    now = time.time()
    ui_settings.update({"notify": {"enabled": True, "apps": ["WhatsApp"], "model_judge": False, "remind_after_min": 10}})
    make_db(world / "wpn.db", [(1, WA, "Sara", "are you coming tonight?", now - 5 * 60), (2, WA, "Omar", "lol", now - 20 * 60)])
    nw.process(now)
    assert nw.current_alert(now) is None                              # five minutes is not long enough, and "lol" asks nothing
    alert = nw.current_alert(now + 6 * 60)
    assert alert and alert["who"] == "Sara" and alert["app"] == "WhatsApp" and alert["minutes"] >= 10
    nw.act(alert["id"], "shown", now + 6 * 60)
    assert nw.current_alert(now + 7 * 60) is None                     # not again for the repeat time
    assert nw.current_alert(now + 6 * 60 + 16 * 60)["who"] == "Sara"


def test_a_message_from_someone_who_matters_or_an_urgent_word_is_reminded_sooner(world):
    now = time.time()
    ui_settings.update({"notify": {"enabled": True, "apps": ["WhatsApp"], "chats": ["Mum"], "model_judge": False, "important_after_min": 2}})
    make_db(world / "wpn.db", [(1, WA, "Mum", "dinner at 8", now - 3 * 60), (2, WA, "Mum Backup", "hi", now - 3 * 60)])
    nw.process(now)
    a = nw.current_alert(now)
    assert a and a["important"] and a["why"] == "from someone on your list"


def test_opening_the_app_clearing_the_notification_or_pressing_seen_settles_it(world):
    now = time.time()
    ui_settings.update({"notify": {"enabled": True, "apps": ["WhatsApp"], "model_judge": False}})
    make_db(world / "wpn.db", [(1, WA, "Sara", "ok?", now - 20 * 60), (2, WA, "Lina", "ok?", now - 20 * 60), (3, WA, "Omar", "ok?", now - 20 * 60)])
    nw.process(now)
    assert len(nw.missed()) == 3
    nw.note_focus("WhatsApp.exe", now)                                # WhatsApp came to the front: all three were seen
    nw.process(now + 1)
    assert nw.missed() == []
    nw.forget_all()
    nw._focus.clear()
    nw.process(now)
    nw.act(nw.missed()[0]["id"], "seen")
    assert len(nw.missed()) == 2
    (world / "wpn.db").unlink()                                       # the notifications were cleared in Windows
    make_db(world / "wpn.db", [])
    nw.process(now + 2)
    assert nw.missed() == []


def test_snooze_mute_and_do_not_disturb(world):
    now = time.time()
    ui_settings.update({"notify": {"enabled": True, "apps": ["WhatsApp"], "model_judge": False}})
    make_db(world / "wpn.db", [(1, WA, "Group chat", "who is free?", now - 30 * 60)])
    nw.process(now)
    a = nw.current_alert(now)
    nw.act(a["id"], "snooze", now)
    assert nw.current_alert(now + 60) is None and nw.current_alert(now + 11 * 60)
    ui_settings.update({"do_not_disturb": True})
    assert nw.current_alert(now + 11 * 60) is None                    # quiet means quiet
    ui_settings.update({"do_not_disturb": False})
    nw.act(a["id"], "mute")
    assert "Group chat" in nw.settings()["muted"] and nw.current_alert(now + 60 * 60) is None


def test_the_model_decides_what_the_rules_could_not_and_never_when_the_cloud_would_see_it(world, monkeypatch):
    now = time.time()
    ui_settings.update({"notify": {"enabled": True, "apps": ["WhatsApp"], "model_judge": True}})
    make_db(world / "wpn.db", [(1, WA, "Boss", "send me the file when you can", now - 30 * 60)])
    asked = []

    def judge(app, title, text):
        asked.append(title)
        return {"important": True, "needs_reply": True, "why": "work request"}

    nw.process(now, judge=judge)
    assert asked == ["Boss"] and nw.current_alert(now)["important"] is True
    # with the cloud brain and no permission, the model is not asked
    from core import cloud_brain
    monkeypatch.setattr(cloud_brain, "active", True)
    nw.forget_all()
    asked.clear()
    nw.process(now, judge=judge)
    assert asked == []


def test_the_message_settings_are_checked(world):
    ui_settings.update({"notify": {"apps": ["WhatsApp", " Telegram "], "remind_after_min": 5}})
    assert ui_settings.get("notify")["apps"] == ["WhatsApp", "Telegram"]
    for bad in ({"remind_after_min": 0}, {"apps": "WhatsApp"}, {"enabled": "yes"}, {"colour": "red"}, {"keywords": ["x" * 80]}):
        with pytest.raises(ui_settings.SettingsError):
            ui_settings.update({"notify": bad})


def test_the_api_shows_what_waits_and_takes_what_the_user_pressed(world, monkeypatch):
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from api import notify_api
    monkeypatch.setattr(notify_api, "_require_local", lambda request: None)
    monkeypatch.setattr(nw, "bring_to_front", lambda app: True)
    now = time.time()
    ui_settings.update({"notify": {"enabled": True, "apps": ["WhatsApp"], "model_judge": False}})
    make_db(world / "wpn.db", [(1, WA, "Sara", "are you coming?", now - 30 * 60)])
    nw.process(now)

    async def go():
        app = web.Application()
        notify_api.register(app)
        async with TestClient(TestServer(app)) as c:
            st = await (await c.get("/ui/notifications")).json()
            assert st["ok"] and st["waiting"][0]["title"] == "Sara" and st["alert"]["who"] == "Sara"
            assert {"name": "WhatsApp", "seen": 1, "watched": True} in st["apps"]
            rid = st["waiting"][0]["id"]
            assert (await (await c.post("/ui/notifications", json={"action": "open", "id": rid})).json())["ok"] is True
            assert (await (await c.get("/ui/notifications")).json())["waiting"] == []           # opening it settled it
            assert (await c.post("/ui/notifications", json={"action": "explode", "id": rid})).status == 404
            assert (await (await c.post("/ui/notifications", json={"action": "clear"})).json())["recent"] == []
    asyncio.run(go())
