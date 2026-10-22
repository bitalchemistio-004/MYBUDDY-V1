"""The brain (S101/S102/S104/S95): an Obsidian vault checked every turn and
learned into. A tmp vault and tmp settings; the web, the model, speech and the
Recycle Bin are fakes."""
import json

import pytest

from brain import brain_store as bs
from brain.obsidian_vault import plain
from core import ui_settings


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "cfg" / "app_settings.json"))
    ui_settings.reset_cache()
    root = tmp_path / "Obsidian Vault"
    root.mkdir()
    appdata = tmp_path / "appdata"
    (appdata / "obsidian").mkdir(parents=True)
    (appdata / "obsidian" / "obsidian.json").write_text(
        json.dumps({"vaults": {"a1": {"path": str(root), "ts": 1, "open": True}}}), encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(bs, "_vault", None)
    monkeypatch.setattr(bs, "_vault_root", None)
    monkeypatch.setattr(bs, "_learning", set())
    trashed = []
    monkeypatch.setattr(bs, "_trash", lambda p: (trashed.append(p), p.unlink()))
    yield root, trashed
    ui_settings.reset_cache()


def test_the_vault_obsidian_has_open_is_linked_and_mbuddy_writes_only_in_its_folder(vault):
    root, _ = vault
    state = bs.status()
    assert state["linked"] and state["vault"] == str(root)
    assert state["folder"] == str(root / "MBUDDY") and (root / "MBUDDY" / "Knowledge").is_dir()
    assert bs.unlink()["linked"] is False and bs.get_vault() is None
    assert bs.link()["linked"] is True


def test_recall_brings_at_most_two_plain_notes(vault):
    v = bs.get_vault()
    for i, title in enumerate(["Notion", "Notion templates", "Notion shortcuts"]):
        v.upsert("Knowledge", title, f"## What it is\nNotion is a notes app, see [[Obsidian|another app]]. {i}")
    block, used = bs.recall("how do I use Notion")
    assert block.startswith("FROM YOUR NOTES") and len(used) == 2
    assert "[[" not in block and "##" not in block and "another app" in block


def test_search_notes_is_a_plain_lookup(vault):
    bs.get_vault().upsert("Apps", "Blender", "## What it is\nA 3D modelling app.")
    assert "Blender" in bs.search_notes("blender")
    assert bs.search_notes("kubernetes") == "Nothing in my notes about kubernetes yet."


def test_the_brain_is_checked_before_learning(vault, monkeypatch):
    started = []
    monkeypatch.setattr(bs, "_learn_job", lambda *a: started.append(a))
    monkeypatch.setattr(bs.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda s: target(*args)})())
    # a note the user wrote counts as known by its words alone, with or without the embedding model installed
    bs.get_vault().upsert("Knowledge", "ordering pizza", "## What it is\nOrdering pizza online.", source="user")
    assert bs.learn_about("ordering pizza").startswith("I already have notes") and not started
    out = bs.learn_about("smart lights", why="turn off the lights")
    assert out.startswith("That's new to me.") and started == [("smart lights", "topic", "turn off the lights")]


def test_asking_again_while_learning_says_so(vault):
    bs._learning.add("canva")
    assert bs.learn_about("Canva").startswith("I'm still learning about Canva")


def test_the_learning_job_writes_a_note_with_its_sources_and_comes_back(vault, monkeypatch):
    said = []
    monkeypatch.setattr(bs, "_search", lambda topic, kind: [
        {"title": "Canva guide", "href": "https://example.com/canva", "body": "Canva is a design tool."}])
    monkeypatch.setattr(bs, "_read_page", lambda url: "Canva lets you design posters. Open it, pick a template.")
    monkeypatch.setattr(bs, "_wait_until_idle", lambda *a: None)
    monkeypatch.setattr(bs, "_write_note_text", lambda topic, why, found, pages: (
        "## What it is\nCanva is an online design tool.\n## How to do it\n1. Open canva.com\n"
        "## What I can do about it\nI can open canva.com in your browser."))
    monkeypatch.setattr(bs, "_announce", lambda speech, topic, ok: said.append((speech, ok)))
    bs._learning.add("canva")
    bs._learn_job("Canva", "app", "")
    note = bs.get_vault().base / "Apps" / "Canva.md"
    text = note.read_text(encoding="utf-8")
    assert "## Sources" in text and "https://example.com/canva" in text and "mbuddy/learned" in text
    assert said and said[0][1] and "I learned about Canva" in said[0][0] and "online design tool" in said[0][0]
    assert "canva" not in bs._learning


def test_nothing_found_is_said_honestly_and_writes_nothing(vault, monkeypatch):
    said = []
    monkeypatch.setattr(bs, "_search", lambda topic, kind: [])
    monkeypatch.setattr(bs, "_announce", lambda speech, topic, ok: said.append((speech, ok)))
    bs._learn_job("zzzq", "topic", "")
    assert said and not said[0][1] and "couldn't find" in said[0][0]
    assert not list((bs.get_vault().base / "Knowledge").glob("*.md"))


def test_the_learning_rate_follows_the_turns_outcome(vault):
    v = bs.get_vault()
    path = v.upsert("Knowledge", "Canva", "## What it is\nA design tool.")
    start = v.read(path)[0]["strength"]
    bs.note_used([path], success=True)
    up = v.read(path)[0]["strength"]
    bs.note_used([path], success=False)
    down = v.read(path)[0]["strength"]
    assert up > start and down < up


def test_removing_an_app_takes_its_notes_to_the_recycle_bin(vault):
    root, trashed = vault
    bs.get_vault().upsert("Apps", "Canva", "## What it is\nA design tool.")
    assert bs.forget_app("Canva") is True and len(trashed) == 1
    assert bs.forget_app("Canva") is False


def test_deleting_is_only_inside_mbuddys_folder(vault):
    root, trashed = vault
    mine = bs.get_vault().upsert("Knowledge", "Canva", "x")
    users = root / "My diary.md"
    users.write_text("private", encoding="utf-8")
    assert bs.delete_note(str(users)) is False and users.exists()
    assert bs.delete_note(str(mine)) is True and trashed == [mine]


def test_plain_text_for_the_model():
    assert plain("## Steps\nSee [[Word]] and [[Obsidian|my notes]].") == "Steps:\nSee Word and my notes."


def test_the_folder_readme_is_not_a_note(vault):
    v = bs.get_vault()
    assert (v.base / "About this folder.md").is_file()
    assert bs.list_notes() == [] and bs.status()["notes"] == 0
    assert bs.recall("what does MBUDDY keep in this folder") == ("", [])
