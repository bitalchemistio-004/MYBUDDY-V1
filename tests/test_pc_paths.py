r"""Paths the way people say them (the field test, 2026-10-19: 'Documents\MBUDDY' was reported as missing)."""
import pytest

from brain import pc_actions


@pytest.fixture
def home(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    base = docs / "MBUDDY"
    (base / "Screenshots").mkdir(parents=True)
    (base / "notes.txt").write_text("x")
    (base / "~$notes.docx").write_text("lock")
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(pc_actions, "BASE_DIR", str(base))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("said,where", [(r"Documents\MBUDDY", "Documents/MBUDDY"), ("MBUDDY", "Documents/MBUDDY"),
                                        (r"MBUDDY\Screenshots", "Documents/MBUDDY/Screenshots"), ("notes.txt", "Documents/MBUDDY/notes.txt"),
                                        ("Desktop", "Desktop"), ("Documents", "Documents")])
def test_folders_as_people_name_them(home, said, where):
    assert pc_actions._resolve_path(said) == home / where


def test_listing_leaves_out_office_lock_files(home):
    out = pc_actions._list_folder({"path": r"Documents\MBUDDY"})
    assert "has 2 items" in out and "~$" not in out
