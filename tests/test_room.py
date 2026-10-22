"""The room: points are earned XP minus what was spent, and nothing in the room ever pays points."""
import pytest

from core import rewards, room


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_REWARDS_PATH", str(tmp_path / "r.json"))
    monkeypatch.setenv("MBUDDY_ROOM_PATH", str(tmp_path / "room.json"))
    rewards.reset()
    room.reset()


def test_a_new_room_has_every_place_at_level_one_and_no_points():
    s = room.state(xp=0, level=1)
    assert s["points"] == 0 and all(lv == 1 for lv in s["levels"]["blink"].values())
    assert s["owned"]["blink"] == {}


def test_an_upgrade_costs_points_and_never_lowers_the_level():
    ok, why = room.upgrade("blink", "plant", xp=20, level=1)
    assert not ok and "30 points" in why
    assert room.upgrade("blink", "plant", xp=30, level=1) == (True, "")
    s = room.state(xp=30, level=1)
    assert s["levels"]["blink"]["plant"] == 2 and s["points"] == 0
    assert s["level"] == 1                                         # spending does not touch the level
    assert s["log"][-1]["what"] == "plant" and s["log"][-1]["cost"] == 30


def test_the_total_cost_of_a_place_is_the_sum_of_its_levels():
    for _ in range(4):
        assert room.upgrade("void", "desk", xp=10_000, level=9) == (True, "")
    assert room.state(xp=10_000, level=9)["spent"] == 30 + 60 + 120 + 240
    ok, why = room.upgrade("void", "desk", xp=10_000, level=9)
    assert not ok and "fully upgraded" in why


def test_the_shop_checks_level_price_and_ownership():
    ok, why = room.buy("blink", "pet", xp=500, level=2)
    assert not ok and "level 5" in why
    ok, why = room.buy("blink", "clock", xp=10, level=1)
    assert not ok and "40 points" in why
    assert room.buy("blink", "clock", xp=100, level=1) == (True, "")
    assert room.buy("blink", "clock", xp=100, level=1)[0] is False
    assert room.state(xp=100, level=1)["points"] == 60
    assert room.state(xp=100, level=1)["owned"]["void"] == {}      # each character has its own room


def test_moving_things_and_putting_them_back():
    assert room.move("blink", "lamp", 36) == (True, "")
    assert room.state(xp=0, level=1)["layout"]["blink"]["lamp"] == 36
    assert room.move("blink", "radio", 6)[0] is False               # not bought
    room.put_back("blink")
    assert room.state(xp=0, level=1)["layout"]["blink"] == {}


def test_games_keep_scores_and_trophies_but_pay_nothing():
    room.record_game("memory", 18, better_is_lower=True)
    room.record_game("memory", 22, better_is_lower=True)
    room.record_game("catch", 7)
    room.record_game("catch", 5)
    room.trophy("catch"); room.find_egg("star")
    s = room.state(xp=0, level=1)
    assert s["best"] == {"memory": 18, "catch": 7} and s["trophies"] == {"catch": True} and s["eggs"] == {"star": True}
    assert s["points"] == 0


def test_balance_reads_the_real_rewards():
    rewards.award("task_done", "t1")
    rewards.award("focus_sprint", "s1")
    assert room.balance() == 35
