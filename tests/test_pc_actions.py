"""PC abilities: low-risk ones run and report what really happened; critical
ones never act without the user's yes for that exact call (S85, S86).
Everything that would touch the PC is mocked."""
import os
import time
import webbrowser

import pytest

from brain import pc_actions as pa
from brain.tools import ToolFailure


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    pa._pending = None
    monkeypatch.setattr(pa, "BASE_DIR", tmp_path / "MBUDDY")
    monkeypatch.setattr(pa, "_windows", lambda: [])              # no real window enumeration
    monkeypatch.setattr(pa, "_wait_for_window", lambda before, words, timeout=5.0: None)
    monkeypatch.setattr(pa, "_publish", lambda: None)             # don't touch runtime_status

    def no_real_actions(*a, **k):
        raise AssertionError("a real PC action was attempted in a test")
    monkeypatch.setattr(os, "startfile", no_real_actions, raising=False)
    monkeypatch.setattr(pa, "_open", no_real_actions)
    monkeypatch.setattr(pa.subprocess, "Popen", no_real_actions)
    monkeypatch.setattr(pa.subprocess, "run", no_real_actions)
    monkeypatch.setattr(webbrowser, "open", no_real_actions)
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)
    yield
    pa._pending = None


# ── the critical gate ────────────────────────────────────────────────────
def test_critical_delete_never_acts_without_confirmation(monkeypatch, tmp_path):
    victim = tmp_path / "report.txt"
    victim.write_text("keep me")
    recycled = []
    monkeypatch.setattr(pa, "_recycle", lambda p: recycled.append(p))

    out = pa.run("delete_file", {"path": str(victim)})

    assert out.startswith("NOT DONE YET")
    assert isinstance(out, pa.NeedsConfirmation) and not isinstance(out, ToolFailure)
    assert "Delete report.txt from " in out and "It goes to the Recycle Bin." in out
    assert recycled == [] and victim.exists()
    assert pa.pending() is out.pending and out.pending.action == "delete_file"


@pytest.mark.parametrize("name,args", [
    ("set_brightness", {"percent": 40}),
    ("lock_screen", {}),
    ("power", {"action": "shutdown"}),
    ("power", {"action": "restart"}),
    ("power", {"action": "sleep"}),
])
def test_every_critical_ability_only_asks(monkeypatch, name, args):
    executed = []
    monkeypatch.setitem(pa.REGISTRY, name, pa.Ability(
        **{**pa.REGISTRY[name].__dict__, "execute": lambda plan: executed.append(plan) or "done"}))
    out = pa.run(name, args)                   # confirmed defaults to False
    assert out.startswith("NOT DONE YET") and executed == []
    out = pa.run(name, args, confirmed=False)
    assert out.startswith("NOT DONE YET") and executed == []


def test_kill_app_asks_and_does_not_terminate(monkeypatch):
    class Proc:
        pid, info = 77, {"name": "Spotify.exe", "pid": 77}

        def terminate(self):
            raise AssertionError("terminated without a yes")
    monkeypatch.setattr("psutil.process_iter", lambda attrs=None: [Proc()])
    monkeypatch.setattr("brain.pc_windows.find_window", lambda t: None)
    out = pa.run("kill_app", {"name": "spotify"})
    assert out.startswith("NOT DONE YET") and "Force-close spotify?" in out


def test_all_critical_abilities_have_a_question_step():
    critical = [a for a in pa.REGISTRY.values() if a.risk == "critical"]
    assert {a.name for a in critical} >= {"set_brightness", "delete_file", "kill_app", "lock_screen", "power"}
    assert all(a.prepare is not None for a in critical)


def test_yes_runs_exactly_what_was_asked_and_no_approval_is_remembered(monkeypatch, tmp_path):
    victim = tmp_path / "a.txt"
    victim.write_text("x")
    recycled = []
    monkeypatch.setattr(pa, "_recycle", lambda p: recycled.append(p) or os.remove(p))

    pa.run("delete_file", {"path": str(victim)})
    assert pa.resolve("good luck!") is None and pa.pending() is not None   # not an answer: keeps waiting
    name, args, result = pa.resolve("yes")
    assert name == "delete_file" and recycled == [str(victim)]
    assert result.startswith("Moved") and "Recycle Bin" in result and args["target"] == "a.txt"
    assert pa.pending() is None

    other = tmp_path / "b.txt"
    other.write_text("y")
    out = pa.run("delete_file", {"path": str(other)})                     # a second call asks again
    assert out.startswith("NOT DONE YET") and other.exists() and recycled == [str(victim)]


def test_no_cancels_and_expired_question_is_dropped(monkeypatch, tmp_path):
    victim = tmp_path / "c.txt"
    victim.write_text("x")
    monkeypatch.setattr(pa, "_recycle", lambda p: pytest.fail("recycled after a no"))
    pa.run("delete_file", {"path": str(victim)})
    name, _args, result = pa.resolve("no")
    assert result.startswith("Cancelled") and victim.exists()

    pa.run("delete_file", {"path": str(victim)})
    pa._pending.expires = time.time() - 1
    assert pa.pending() is None and pa.resolve("yes") is None and victim.exists()


def test_power_executor_refuses_under_pytest(monkeypatch):
    monkeypatch.setenv("MBUDDY_ALLOW_POWER", "1")
    out = pa.run("power", {"action": "shutdown"}, confirmed=True)
    assert out.startswith("FAILED:")


# ── create_file ──────────────────────────────────────────────────────────
def test_create_file_never_overwrites(tmp_path):
    first = pa.run("create_file", {"name": "notes", "content": "one", "kind": "txt"})
    second = pa.run("create_file", {"name": "notes.txt", "content": "two", "kind": "txt"})
    base = tmp_path / "MBUDDY"
    assert first == f"Created {base / 'notes.txt'} (3 bytes)."
    assert str(base / "notes (2).txt") in second
    assert (base / "notes.txt").read_text() == "one"
    assert (base / "notes (2).txt").read_text() == "two"


def test_create_file_stays_in_its_folder_and_makes_real_docx(tmp_path):
    out = pa.run("create_file", {"name": r"..\..\evil", "content": "# Title\nBody line", "kind": "docx"})
    path = tmp_path / "MBUDDY" / "evil.docx"
    assert str(path) in out and path.exists()
    from docx import Document
    doc = Document(str(path))
    assert [p.text for p in doc.paragraphs] == ["Title", "Body line"]
    assert "Body line" in pa.run("read_file", {"path": "evil.docx"})


def test_create_file_rejects_unknown_kind():
    assert pa.run("create_file", {"name": "x", "content": "y", "kind": "exe"}).startswith("FAILED:")


# ── open_app ─────────────────────────────────────────────────────────────
def fake_start_menu(monkeypatch, tmp_path, names):
    menu = tmp_path / "Start Menu" / "Programs"
    (menu / "Accessories").mkdir(parents=True)
    for n in names:
        (menu / ("Accessories" if n == "Notepad" else "") / f"{n}.lnk").write_bytes(b"")
    monkeypatch.setattr(pa, "_start_menu_dirs", lambda: [menu])
    monkeypatch.setattr(pa, "_app_paths", lambda: {})
    monkeypatch.setattr(pa, "_start_apps", lambda: {})
    return menu


def test_open_app_resolves_fake_shortcuts(monkeypatch, tmp_path):
    menu = fake_start_menu(monkeypatch, tmp_path,
                           ["Notepad", "Spotify", "Uninstall Spotify", "Microsoft Word", "Google Chrome"])
    started = []
    monkeypatch.setattr(pa, "_open", lambda p: started.append(p))

    assert pa.run("open_app", {"name": "notepad"}) == "Opened Notepad (window appeared: no, not within 5 seconds)."
    assert pa.run("open_app", {"name": "word"}).startswith("Opened Microsoft Word")
    assert pa.run("open_app", {"name": "spotify"}).startswith("Opened Spotify ")
    assert pa.run("open_app", {"name": "chrom"}).startswith("Opened Google Chrome")
    assert started == [str(menu / "Accessories" / "Notepad.lnk"), str(menu / "Microsoft Word.lnk"),
                       str(menu / "Spotify.lnk"), str(menu / "Google Chrome.lnk")]


def test_open_app_reports_the_window_it_saw(monkeypatch, tmp_path):
    fake_start_menu(monkeypatch, tmp_path, ["Notepad"])
    monkeypatch.setattr(pa, "_open", lambda p: None)
    win = type("W", (), {"hwnd": 5, "title": "Untitled - Notepad", "app": "notepad", "focused": True})()
    monkeypatch.setattr(pa, "_wait_for_window", lambda before, words, timeout=5.0: win)
    assert pa.run("open_app", {"name": "Notepad"}) == 'Opened Notepad (window appeared: yes, "Untitled - Notepad").'


def test_open_app_unknown_name_fails(monkeypatch, tmp_path):
    fake_start_menu(monkeypatch, tmp_path, ["Notepad"])
    out = pa.run("open_app", {"name": "photoshop"})
    assert isinstance(out, ToolFailure) and out.startswith("FAILED:")


# ── web ──────────────────────────────────────────────────────────────────
def test_browser_search_builds_the_url_and_uses_the_browser_the_user_named(monkeypatch):
    launched = []
    monkeypatch.setattr(pa, "_browser_exe", lambda b: r"C:\Chrome\chrome.exe" if b == "chrome" else None)
    monkeypatch.setattr(pa, "_open", lambda cmd, **k: launched.append(cmd))
    out = pa.run("browser_search", {"query": "weather in Rabat & Fes", "browser": "chrome"})
    assert launched == [[r"C:\Chrome\chrome.exe", "https://www.google.com/search?q=weather+in+Rabat+%26+Fes"]]
    assert out.startswith("Opened a Google search for 'weather in Rabat & Fes' in Chrome")


def test_browser_search_falls_back_to_default_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(pa, "_browser_exe", lambda b: None)
    monkeypatch.setattr(pa, "_open", lambda url, **k: opened.append(url) or True)
    out = pa.run("browser_search", {"query": "cats"})
    assert opened == ["https://www.google.com/search?q=cats"] and "default browser" in out


def test_open_url_only_http(monkeypatch):
    opened = []
    monkeypatch.setattr(pa, "_open", lambda url, **k: opened.append(url) or True)
    assert pa.run("open_url", {"url": "file:///C:/Windows/System32"}).startswith("FAILED:")
    assert pa.run("open_url", {"url": "javascript:alert(1)"}).startswith("FAILED:")
    assert pa.run("open_url", {"url": "example.com/page"}).startswith("Opened https://example.com/page")
    assert opened == ["https://example.com/page"]


# ── discovery and specs ──────────────────────────────────────────────────
def test_list_abilities_includes_every_entry_and_existing_tools():
    out = pa.run("list_abilities", {})
    for name, ability in pa.REGISTRY.items():
        assert f"- {name}(" in out and f"[{ability.risk}]" in out
    for existing in ("adjust_volume", "media_control", "window_action", "add_reminder", "pc_status", "web_search"):
        assert existing in out


def test_list_abilities_topic_filter():
    out = pa.run("list_abilities", {"topic": "files"})
    assert "create_file(" in out and "open_app(" not in out
    assert "nothing matched" in pa.run("list_abilities", {"topic": "zzz"})


def test_tool_specs_match_agent_format_and_dont_clash():
    from brain import agent
    specs = pa.tool_specs()
    assert [s["function"]["name"] for s in specs] == list(pa.REGISTRY)
    for s in specs:
        assert s["type"] == "function" and s["function"]["parameters"]["type"] == "object"
        assert set(s["function"]["parameters"]["required"]) <= set(s["function"]["parameters"]["properties"])
    assert not {s["function"]["name"] for s in specs} & {t["function"]["name"] for t in agent.TOOLS}


# ── failures ─────────────────────────────────────────────────────────────
def test_failures_start_with_FAILED(monkeypatch, tmp_path):
    assert pa.run("read_file", {"path": str(tmp_path / "missing.pdf")}).startswith("FAILED:")
    assert pa.run("no_such_ability", {}).startswith("FAILED:")
    assert pa.run("delete_file", {"path": str(tmp_path / "missing.txt")}).startswith("FAILED:")
    assert pa.run("set_brightness", {"percent": 250}).startswith("FAILED:")
    assert pa.run("open_settings", {"page": "qqqqqq"}).startswith("FAILED:")

    def broken(p):
        raise OSError("access denied")
    monkeypatch.setattr(pa, "_open", broken)
    fake_start_menu(monkeypatch, tmp_path, ["Notepad"])
    out = pa.run("open_app", {"name": "Notepad"})
    assert isinstance(out, ToolFailure) and out.startswith("FAILED:") and "access denied" in out


def test_open_settings_uses_ms_settings(monkeypatch):
    started = []
    monkeypatch.setattr(pa, "_open", lambda p: started.append(p))
    assert pa.run("open_settings", {"page": "Bluetooth"}).startswith("Opened Settings > bluetooth")
    pa.run("open_settings", {"page": "wifi"})
    assert started == ["ms-settings:bluetooth", "ms-settings:network-wifi"]


def test_delete_refuses_system_files(monkeypatch):
    windir = os.environ.get("WINDIR", r"C:\Windows")
    out = pa.run("delete_file", {"path": os.path.join(windir, "notepad.exe")})
    assert out.startswith("FAILED:") and pa.pending() is None


def test_a_screenshot_is_saved_as_a_new_file_in_the_default_folder_and_never_overwrites(tmp_path, monkeypatch):
    import sys
    import types
    from brain import pc_actions
    saved = []

    class Img:
        def save(self, path, fmt):
            saved.append((str(path), fmt))
            open(path, "wb").write(b"png")

    fake = types.ModuleType("PIL.ImageGrab")
    fake.grab = lambda all_screens=False: Img()
    monkeypatch.setitem(sys.modules, "PIL.ImageGrab", fake)
    import PIL
    monkeypatch.setattr(PIL, "ImageGrab", fake, raising=False)
    monkeypatch.setattr(pc_actions, "BASE_DIR", tmp_path)
    monkeypatch.setattr(pc_actions.time, "strftime", lambda f: "2026-10-08 20-51-00")
    first = pc_actions.REGISTRY["take_screenshot"].execute({})
    second = pc_actions.REGISTRY["take_screenshot"].execute({})
    assert "Screenshots" in first and first.startswith("Saved a screenshot")
    assert len({p for p, _ in saved}) == 2 and all(p.startswith(str(tmp_path / "Screenshots")) for p, _ in saved)
    assert pc_actions.REGISTRY["take_screenshot"].risk == "low"


def test_reading_a_window_refuses_secrets_and_the_cloud_and_names_what_it_found(monkeypatch):
    from types import SimpleNamespace
    from brain import pc_actions
    from core import cloud_brain, notif_watch, window_reader
    win = SimpleNamespace(app="notepad", title="notes.txt - Notepad", hwnd=1)
    monkeypatch.setattr(pc_actions, "_windows_for_reading", lambda target: win)
    monkeypatch.setattr(window_reader, "read", lambda hwnd: {"texts": ["Buy milk", "Call Sara"], "controls": ["[button] Save"]})
    ok = pc_actions.REGISTRY["read_window"].execute({"app": "notepad"})
    assert "Buy milk" in ok and "[button] Save" in ok and pc_actions.REGISTRY["read_window"].risk == "read"
    monkeypatch.setattr(pc_actions, "_windows_for_reading", lambda target: SimpleNamespace(app="KeePass", title="Database - KeePass", hwnd=2))
    assert "passwords" in str(pc_actions.REGISTRY["read_window"].execute({"app": "keepass"}))
    monkeypatch.setattr(pc_actions, "_windows_for_reading", lambda target: win)
    monkeypatch.setattr(cloud_brain, "active", True)
    monkeypatch.setattr(notif_watch, "settings", lambda: {**notif_watch.DEFAULTS, "share_with_cloud": False})
    assert "cloud brain would see" in str(pc_actions.REGISTRY["read_window"].execute({"app": "notepad"}))
