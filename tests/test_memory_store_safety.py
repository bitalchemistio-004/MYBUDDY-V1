"""The user's memory is never overwritten by a fresh one (2026-10-20: the real snapshot was replaced by an empty one)."""
import asyncio

import memory.long_term as lt
from memory import sqlite_store


def test_a_memory_that_could_not_be_read_is_never_saved(monkeypatch, tmp_path):
    store = sqlite_store.SQLiteMemoryStore(tmp_path / "m.sqlite3")
    real = sqlite_store.default_memory()
    real["core_facts"].append("Sam is a visual engineer")
    store.save(real)
    monkeypatch.setattr(sqlite_store, "_store", store)

    def broken():
        raise OSError("database is locked")
    monkeypatch.setattr(store, "load", broken)
    fresh = lt.load_memory()
    assert fresh["core_facts"] == []
    asyncio.run(lt.save_memory(fresh))
    assert sqlite_store.SQLiteMemoryStore(tmp_path / "m.sqlite3").load()["core_facts"] == ["Sam is a visual engineer"]


def test_tests_never_touch_the_real_memory():
    from pathlib import Path
    assert Path(sqlite_store.get_memory_store().db_path).resolve() != Path(sqlite_store.DB_PATH).resolve()


def test_the_fact_table_mirrors_the_snapshot(tmp_path):
    # F14: Forget removed a fact from the snapshot, but the table kept it forever, so counts never agreed
    import sqlite3
    store = sqlite_store.SQLiteMemoryStore(tmp_path / "m.sqlite3")
    mem = sqlite_store.default_memory()
    mem["core_facts"] += ["Sam lives in Tripoli", "Sam is a visual engineer"]
    store.save(mem)
    mem["core_facts"].remove("Sam lives in Tripoli")
    store.save(mem)
    rows = [r[0] for r in sqlite3.connect(tmp_path / "m.sqlite3").execute("select fact from core_facts")]
    assert rows == ["Sam is a visual engineer"]
