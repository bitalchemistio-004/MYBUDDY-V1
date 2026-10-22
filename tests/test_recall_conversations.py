"""find_anything searches past conversations too (the field test, 2026-10-19: 'what did I ask you on Oct 12' and
'what did I ask about music earlier' both said there was no record, though both were in the conversation log)."""
import sqlite3
from datetime import date

import pytest

from brain.connectors import day


@pytest.fixture
def log(tmp_path, monkeypatch):
    db = tmp_path / "mbuddy.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversation_log (id INTEGER PRIMARY KEY, uid TEXT, time TEXT, user TEXT, mbuddy TEXT, lang TEXT)")
    rows = [("2026-10-12 17:39", "Research the iPhone 18 Pro Max and put it in a Word document", "Wrote 321 words into Document1."),
            ("2026-10-18 20:33", "play me some songs im feeling low and sad", "I opened a search in YouTube Music."),
            ("2026-10-19 22:58", "add a task send the deck", "Added task #44: send the deck")]
    con.executemany("INSERT INTO conversation_log (time, user, mbuddy, lang) VALUES (?, ?, ?, 'en')", rows)
    con.commit(); con.close()
    monkeypatch.setattr(day, "_conversation_db", lambda: db)
    monkeypatch.setattr(day, "_day_asked", lambda q, today=None, f=day._day_asked: f(q, date(2026, 10, 19)))
    return db


def test_a_date_finds_what_was_said_that_day(log):
    out = day.past_conversations("on October 12 what did I ask you")
    assert len(out) == 1 and "iPhone 18 Pro Max" in out[0]


def test_words_find_it_on_any_day(log):
    assert "iPhone" in day.past_conversations("the iphone research")[0]


def test_the_wrong_day_says_so_and_shows_where_it_was(log):
    out = day.past_conversations("what did I ask you earlier today about songs")
    assert out[0].startswith("(nothing about that on Mon 19 Oct)") and "low and sad" in out[1]


def test_ask_is_not_found_inside_task(log):
    out = day.past_conversations("what did I ask you about the deck")
    assert out and "send the deck" in out[0]
    assert day.past_conversations("ask") == []


def test_a_day_alone_lists_that_day(log):
    out = day.past_conversations("what did we talk about yesterday")
    assert len(out) == 1 and "low and sad" in out[0]
