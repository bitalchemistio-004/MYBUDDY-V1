import time

import pytest

from brain import brain_store, dialog
from brain.obsidian_vault import Vault


@pytest.fixture(autouse=True)
def clean():
    dialog.reset()
    yield
    dialog.reset()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = Vault(tmp_path / "vault")
    v.init()
    monkeypatch.setattr(brain_store, "get_vault", lambda: v)
    return v


@pytest.mark.parametrize("said", [
    "I want you to write something", "write something", "take a note", "write this down", "I'm going to dictate an email",
    "can you take a note", "please write something down", "start a note", "let's write a letter", "dictate",
])
def test_the_bare_requests_open_a_dictation(said):
    kind, content = dialog.detect_start(said) or (None, None)
    assert kind in ("note", "document") and content == ""


@pytest.mark.parametrize("said, content", [
    ("take a note: buy milk and eggs", "buy milk and eggs"),
    ("make a quick note that I called Sara", "I called Sara"),
    ("write this down: the code is 4471", "the code is 4471"),
    ("jot down call the florist before noon", "call the florist before noon"),
])
def test_a_note_with_its_content_in_one_breath(said, content):
    assert dialog.detect_start(said) == ("note", content)


@pytest.mark.parametrize("said", [
    "write me a short poem about the sea", "write a thank you note to my teacher", "write an email to Sara declining Friday",
    "take me to the airport", "make a timer for ten minutes", "put the music on", "save the file", "keep going",
    "I'll write the report tomorrow", "what's the weather", "play some jazz", "write the report", "set a timer",
])
def test_composing_and_other_commands_are_not_dictation(said):
    assert dialog.detect_start(said) is None


def test_the_whole_dictation_saves_and_checks_the_note(vault):
    t = dialog.handle("take a note")
    assert t.state == "listening" and "listening" in t.say.lower() and dialog.active()["kind"] == "note"
    assert dialog.handle("buy a birthday present for Sara").state == "listening"
    assert dialog.handle("and call the florist before Friday").say in dialog._ACKS
    done = dialog.handle("that's it")
    assert done.tag == "HAPPY" and "Saved to your notes" in done.say and dialog.active() is None
    notes = [p for p in vault.notes() if p.parent.name == "Knowledge"]
    assert len(notes) == 1
    body = vault.read(notes[0])[1]
    assert "birthday present for Sara" in body and "florist" in body


def test_cancel_drops_it_and_nothing_is_saved(vault):
    dialog.handle("write something")
    dialog.handle("this is private")
    assert "Nothing was saved" in dialog.handle("never mind").say
    assert not [p for p in vault.notes() if p.parent.name == "Knowledge"]


def test_done_with_nothing_said_says_so(vault):
    dialog.handle("take a note")
    assert "nothing to save" in dialog.handle("done").say.lower()


def test_a_note_in_one_breath_is_saved_at_once(vault):
    t = dialog.handle("take a note: the vendor contract is due March 9")
    assert "Saved to your notes" in t.say
    assert any("March 9" in vault.read(p)[1] for p in vault.notes() if p.parent.name == "Knowledge")


def test_a_failed_save_is_never_claimed(monkeypatch):
    monkeypatch.setattr(brain_store, "get_vault", lambda: None)
    dialog.handle("take a note")
    dialog.handle("something")
    t = dialog.handle("done")
    assert t.tag == "SAD" and "couldn't save" in t.say.lower()


def test_an_old_dictation_expires_and_the_words_are_an_ordinary_turn(vault):
    dialog.handle("take a note")
    dialog._state.last = time.time() - dialog.IDLE_S - 5
    assert dialog.active() is None
    assert dialog.handle("what's the weather like") is None


def test_ordinary_words_pass_through_when_nothing_is_open():
    assert dialog.handle("hello there") is None
