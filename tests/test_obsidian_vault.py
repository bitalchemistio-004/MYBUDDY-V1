"""Prototype Obsidian-vault brain (S87): frontmatter, learning rate, budget, user edits.

Every test uses pytest's tmp_path; no real vault is read or written.
"""
from datetime import datetime, timedelta

import pytest

from brain.obsidian_vault import (
    END_MARK, START_MARK, OutsideVaultError, Vault, estimate_tokens, join_note, split_note,
)

T0 = datetime(2026, 9, 27, 12, 0)


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "vault")
    v.init()
    return v


def _meta(vault, path):
    return vault.read(path)[0]


# ------------------------------------------------------------- frontmatter

def test_frontmatter_round_trip_keeps_values_and_unknown_keys():
    meta = {"type": "fact", "strength": 0.5, "uses": 2, "tags": ["a", "b"],
            "last_used": "2026-09-27T12:00", "my_own_key": "kept"}
    body = "Some text\n[[Sam]]\n"
    parsed, rest = split_note(join_note(meta, body))
    assert parsed == meta
    assert rest == body


def test_broken_frontmatter_is_never_written(vault):
    path = vault.path_for("Facts", "Broken")
    path.write_text("---\nstrength: [unclosed\n---\nuser text\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    vault.upsert("Facts", "Broken", "mbuddy text", tags=["x"])
    assert vault.reinforce(path) is None
    assert path.read_text(encoding="utf-8") == before


def test_new_note_has_learning_frontmatter(vault):
    path = vault.upsert("Facts", "Sam likes football", "Sam said he plays on Sundays.",
                        tags=["sport"])
    meta = _meta(vault, path)
    assert meta["status"] == "candidate"
    assert meta["strength"] == 0.5
    assert meta["uses"] == 0 and meta["successes"] == 0
    assert meta["source"] == "mbuddy"
    assert meta["tags"] == ["sport"]


# -------------------------------------------------------- learning rate math

def test_reinforce_success_and_failure_math(vault):
    path = vault.upsert("Facts", "Fact A", "a")
    m = vault.reinforce(path, success=True, now=T0)
    assert m["strength"] == pytest.approx(0.6)
    assert m["uses"] == 1 and m["successes"] == 1 and m["streak"] == 1
    m = vault.reinforce(path, success=False, now=T0)
    assert m["strength"] == pytest.approx(0.3)
    assert m["failures"] == 1 and m["streak"] == 0
    for _ in range(10):
        m = vault.reinforce(path, success=True, now=T0)
    assert m["strength"] == pytest.approx(1.0)  # capped at the ceiling


def test_decay_fixed_days_math(vault):
    path = vault.upsert("Facts", "Fact B", "b")
    vault.decay_all(days=7, now=T0)
    assert _meta(vault, path)["strength"] == pytest.approx(0.5 * 0.95, abs=1e-4)
    vault.decay_all(days=14, now=T0)
    assert _meta(vault, path)["strength"] == pytest.approx(0.5 * 0.95 ** 3, abs=1e-4)


def test_decay_by_timestamps_is_idempotent(vault):
    path = vault.upsert("Facts", "Fact C", "c")
    vault.reinforce(path, now=T0)  # strength 0.6, last_decay T0
    later = T0 + timedelta(days=28)
    assert vault.decay_all(now=later) == 1
    s = _meta(vault, path)["strength"]
    assert s == pytest.approx(0.6 * 0.95 ** 4, abs=1e-4)
    assert vault.decay_all(now=later) == 0  # same moment again: no second decay
    assert _meta(vault, path)["strength"] == s


def test_user_notes_never_decay_out_of_view(vault):
    path = vault.upsert("Preferences", "Direct answers", "Sam wants answers first.",
                        source="user")
    vault.decay_all(days=365 * 3, now=T0)
    meta = _meta(vault, path)
    assert meta["strength"] == pytest.approx(0.4)
    assert meta["status"] == "known"


# ---------------------------------------------------------------- thresholds

def test_promotion_after_three_uncorrected_successes(vault):
    path = vault.upsert("Facts", "Fact D", "d")
    vault.reinforce(path, now=T0)
    vault.reinforce(path, now=T0)
    assert _meta(vault, path)["status"] == "candidate"
    vault.reinforce(path, now=T0)
    assert _meta(vault, path)["status"] == "known"


def test_a_correction_resets_the_streak(vault):
    path = vault.upsert("Facts", "Fact E", "e")
    vault.reinforce(path, now=T0)
    vault.reinforce(path, now=T0)
    vault.reinforce(path, success=False, now=T0)
    vault.reinforce(path, now=T0)
    vault.reinforce(path, now=T0)
    assert _meta(vault, path)["status"] == "candidate"  # streak is 2, not 4


def test_skill_needs_user_approval_to_be_promoted(vault):
    path = vault.export_skill("Next song", [{"tool": "media", "args": {"key": "next"},
                                             "check": "title changed"}])
    for _ in range(3):
        vault.reinforce(path, now=T0)
    meta = _meta(vault, path)
    assert meta["status"] == "candidate"
    assert meta["ready_for_review"] is True
    assert vault.can_replay(path) is False


def test_confirmed_skill_starts_known_and_can_replay(vault):
    path = vault.export_skill("Dark mode", ["Open Settings > Personalisation > Colours",
                                            "Choose Dark"], when="Sam asks for dark mode",
                              confirmed_by_user=True)
    meta, body, _ = vault.read(path)
    assert meta["strength"] == 1.0 and meta["status"] == "known"
    assert "1. Open Settings" in body and "2. Choose Dark" in body
    assert vault.can_replay(path) is True
    vault.reinforce(path, success=False, now=T0)  # 0.7: still at the run threshold
    assert vault.can_replay(path) is True
    vault.reinforce(path, success=False, now=T0)  # 0.4: re-plans with the user instead
    assert vault.can_replay(path) is False


def test_forgetting_archives_below_threshold_and_keeps_the_file(vault):
    path = vault.upsert("Facts", "Fact F", "f")
    vault.reinforce(path, success=False, now=T0)  # 0.2
    vault.decay_all(days=7 * 20, now=T0)          # 0.2 * 0.95**20 = 0.072
    meta = _meta(vault, path)
    assert meta["status"] == "archived"
    assert path.exists()
    assert vault.retrieve("fact", 500) == []


# -------------------------------------------------------------------- budget

def test_retrieve_respects_token_budget(vault):
    for i in range(30):
        vault.upsert("Facts", f"Football fact {i}", "football " * 60)
    for budget in (40, 80, 150, 300):
        results = vault.retrieve("football", token_budget=budget, max_notes=30)
        block = Vault.render(results)
        assert results
        assert estimate_tokens(block) <= budget
    assert vault.retrieve("football", token_budget=10) == []  # not even the header fits


def test_retrieve_ranks_by_keywords_and_strength_and_follows_links(vault):
    strong = vault.upsert("People", "Sam", "Builds MBUDDY. Plays football. See [[Barcelona]].")
    vault.upsert("Facts", "Barcelona", "Sam's favourite club.")
    vault.upsert("Facts", "Weather habit", "Sam checks the weather at 8.")
    for _ in range(3):
        vault.reinforce(strong, now=T0)
    results = vault.retrieve("does bitalchemist like football", token_budget=300)
    titles = [r["title"] for r in results]
    assert titles[0] == "Sam"
    assert "Barcelona" in titles  # reached by one link hop, no keyword of its own


def test_weak_notes_are_not_shown(vault):
    path = vault.upsert("Facts", "Tea preference", "Sam drinks tea.")
    vault.reinforce(path, success=False, now=T0)  # 0.2 < 0.4
    assert vault.retrieve("tea", 300) == []


# ------------------------------------------------------ user edits preserved

def test_user_text_outside_markers_survives_updates(vault):
    path = vault.upsert("People", "Sam", "Builds MBUDDY.")
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "\nMy own note: call mum on Sunday.\n", encoding="utf-8")
    vault.upsert("People", "Sam", "Builds MBUDDY. Likes football.")
    body = vault.read(path)[1]
    assert "My own note: call mum on Sunday." in body
    assert "Likes football." in body


def test_user_edit_inside_markers_stops_mbuddy_rewriting(vault):
    path = vault.upsert("Facts", "Coffee", "Sam drinks coffee.")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("Sam drinks coffee.", "Actually I quit coffee."),
                    encoding="utf-8")
    vault.upsert("Facts", "Coffee", "Sam drinks two coffees a day.")
    meta, body, _ = vault.read(path)
    assert "Actually I quit coffee." in body
    assert "two coffees" not in body
    assert meta["user_edited"] is True


def test_removed_markers_mean_the_user_owns_the_body(vault):
    path = vault.upsert("Facts", "Gym", "Goes to the gym.")
    meta, body, _ = vault.read(path)
    path.write_text(join_note(meta, "Rewritten by me.\n"), encoding="utf-8")
    vault.upsert("Facts", "Gym", "Goes to the gym on Mondays.")
    meta, body, _ = vault.read(path)
    assert body == "Rewritten by me.\n"
    assert START_MARK not in body and END_MARK not in body


def test_frontmatter_merge_keeps_user_values(vault):
    path = vault.upsert("Facts", "Music", "Likes lo-fi.", confidence=0.55, tags=["music"])
    meta, body, _ = vault.read(path)
    meta.update({"confidence": 0.9, "strength": 0.95, "my_key": "mine", "tags": ["music", "mine"]})
    path.write_text(join_note(meta, body), encoding="utf-8")
    vault.upsert("Facts", "Music", "Likes lo-fi.", confidence=0.3, strength=0.1,
                 tags=["study"])
    meta = _meta(vault, path)
    assert meta["confidence"] == 0.9           # the user's value wins
    assert meta["strength"] == 0.95            # learning numbers only change via reinforce/decay
    assert meta["my_key"] == "mine"
    assert meta["tags"] == ["music", "mine", "study"]
    m = vault.reinforce(path, now=T0)          # reinforces the user's number
    assert m["strength"] == pytest.approx(1.0)


# --------------------------------------------------------------------- safety

def test_writes_stay_inside_the_mbuddy_folder(vault, tmp_path):
    outside = tmp_path / "vault" / "Personal.md"
    outside.write_text("private", encoding="utf-8")
    path = vault.upsert("Facts", "../../Personal", "x")
    assert vault.base in path.parents
    assert outside.read_text(encoding="utf-8") == "private"
    with pytest.raises(OutsideVaultError):
        vault._inside(outside)
    with pytest.raises(ValueError):
        vault.path_for("Secrets", "x")


def test_episode_appends_without_duplicates(vault):
    day = datetime(2026, 9, 27)
    path = vault.add_episode(day, ["Set a timer for 10 minutes"])
    vault.add_episode(day, ["Set a timer for 10 minutes", "Added reminder: call mum"])
    body = vault.read(path)[1]
    assert body.count("Set a timer for 10 minutes") == 1
    assert "Added reminder: call mum" in body
    assert path.name == "2026-09-27.md"
