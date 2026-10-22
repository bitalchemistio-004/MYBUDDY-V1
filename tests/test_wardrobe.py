"""The wardrobe: only what was earned can be worn, and the live state never reports anything else."""
import pytest

from core import rewards, wardrobe


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_REWARDS_PATH", str(tmp_path / "r.json"))
    monkeypatch.setenv("MBUDDY_WARDROBE_PATH", str(tmp_path / "w.json"))
    rewards.reset()
    wardrobe.reset()


def prog(**kw):
    base = {"level": 1, "tasks": 0, "sprints": 0, "habits": 0, "streak": 0}
    base.update(kw)
    return base


def test_a_new_user_has_only_the_default_look_unlocked():
    s = wardrobe.state(prog())
    assert s["character"] == "blink"
    assert all(not i["unlocked"] for slot in ("crown", "eyes", "neck", "back", "hand", "aura") for i in s["slots"][slot] if i["id"] in ("crown", "halo", "wizard", "shades"))
    assert [f["id"] for f in s["finishes"] if f["unlocked"]] == ["asis"]


def test_an_unearned_item_is_refused_with_how_to_earn_it():
    ok, why = wardrobe.wear("crown", "halo", prog(level=3))
    assert not ok and "Reach level 6" in why
    assert wardrobe.live(prog(level=3))["worn"]["crown"] == ""


def test_an_earned_item_is_worn_and_survives_reload():
    ok, why = wardrobe.wear("eyes", "blush", prog(level=2))
    assert ok and why == ""
    assert wardrobe.live(prog(level=2))["worn"]["eyes"] == "blush"
    wardrobe._cache["v"] = None
    assert wardrobe.state(prog(level=2))["worn"]["eyes"] == "blush"


def test_unknown_slot_or_item_is_refused():
    assert wardrobe.wear("feet", "boots", prog(level=9))[0] is False
    assert wardrobe.wear("crown", "tiara", prog(level=9))[0] is False


def test_an_item_is_not_reported_if_progress_no_longer_allows_it():
    wardrobe.wear("crown", "beanie", prog(level=4))
    assert wardrobe.live(prog(level=4))["worn"]["crown"] == "beanie"
    assert wardrobe.live(prog(level=1))["worn"]["crown"] == ""          # the choice stays stored, but is not shown


def test_taking_an_item_off_and_everything_off():
    wardrobe.wear("neck", "bell", prog(level=2))
    assert wardrobe.wear("neck", "", prog(level=2)) == (True, "")
    assert wardrobe.live(prog(level=2))["worn"]["neck"] == ""
    wardrobe.wear("neck", "bell", prog(level=2))
    wardrobe.set_finish("ink", prog(level=2))
    wardrobe.take_everything_off()
    s = wardrobe.live(prog(level=2))
    assert s["worn"]["neck"] == "" and s["finish"] == "asis"


def test_finish_and_ink_rules():
    assert wardrobe.set_finish("gold", prog(level=3))[0] is False
    assert wardrobe.set_finish("neon", prog(level=3)) == (True, "")
    assert wardrobe.set_ink("rose") == (True, "")
    assert wardrobe.set_ink("plaid")[0] is False
    s = wardrobe.live(prog(level=3))
    assert s["finish"] == "neon" and s["ink"] == "rose"


def test_progress_reads_real_awards():
    for i in range(12):
        rewards.award("task_done", str(i))
    p = wardrobe.progress()
    assert p["tasks"] == 12 and p["level"] == 1 and p["streak"] >= 1


def test_every_catalog_item_has_a_drawing_id_and_a_reason():
    for slot, rows in wardrobe.ITEMS.items():
        ids = [r[0] for r in rows]
        assert len(ids) == len(set(ids)), slot
        for r in rows:
            assert r[2] in ("level", "tasks", "sprints", "habits", "streak") and r[3] >= 1 and r[4]


def test_each_item_can_have_its_own_colour():
    wardrobe.wear("crown", "beanie", prog(level=4))
    assert wardrobe.set_color("crown", "rose") == (True, "")
    assert wardrobe.set_color("crown", "plaid")[0] is False
    assert wardrobe.set_color("feet", "rose")[0] is False
    live = wardrobe.live(prog(level=4))
    assert live["colors"]["crown"] == "rose" and live["colors"]["eyes"] == ""
    assert wardrobe.set_color("crown", "") == (True, "")                  # follows the general ink again
    assert wardrobe.live(prog(level=4))["colors"]["crown"] == ""


def test_every_item_can_be_pixel_art_but_an_empty_place_cannot():
    ok, why = wardrobe.set_style("crown", "pixel")                         # nothing worn there yet
    assert not ok and "no pixel" in why
    wardrobe.wear("crown", "beanie", prog(level=4))
    assert wardrobe.set_style("crown", "pixel") == (True, "")
    assert wardrobe.live(prog(level=4))["styles"]["crown"] == "pixel"
    assert wardrobe.set_style("crown", "chalk")[0] is False
    assert wardrobe.set_style("crown", "line") == (True, "")
    assert wardrobe.live(prog(level=4))["styles"]["crown"] == "line"        # the classic line drawing is still a choice
    wardrobe.set_style("crown", "pixel")
    wardrobe.wear("crown", "", prog(level=4))
    assert wardrobe.live(prog(level=4))["styles"]["crown"] == "line"        # an empty place is never "pixel"


def test_taking_everything_off_clears_colours_and_styles():
    wardrobe.wear("eyes", "glasses", prog(habits=10))
    wardrobe.set_color("eyes", "mint")
    wardrobe.set_style("eyes", "pixel")
    wardrobe.take_everything_off()
    s = wardrobe.live(prog(habits=10))
    assert s["colors"]["eyes"] == ""
    wardrobe.wear("eyes", "glasses", prog(habits=10))
    assert wardrobe.live(prog(habits=10))["styles"]["eyes"] == "pixel"      # back to the default look: pixel art


def test_pixel_ids_match_real_catalog_items_and_the_drawing_file():
    import re
    from pathlib import Path
    catalog = {f"{slot}-{r[0]}" for cat in (wardrobe.ITEMS, *wardrobe.CHAR_ITEMS.values()) for slot, rows in cat.items() for r in rows}
    assert wardrobe.PIXEL_ITEMS == catalog and len(catalog) == 94           # every catalog item of every character has a pixel drawing
    js = Path("shell/ui/js/buddy/wardrobe-pixel.js").read_text(encoding="utf-8")
    drawn = set(re.findall(r"""^  ["']([a-z]+-[a-z]+)["']: \{""", js, re.M))
    assert drawn == set(wardrobe.PIXEL_ITEMS)


def test_a_new_wardrobe_draws_in_pixel_art():
    s = wardrobe.live(prog(level=4))
    assert all(v == "line" for v in s["styles"].values())                   # nothing is worn yet, so nothing is drawn as pixels
    wardrobe.wear("eyes", "blush", prog(level=4))
    assert wardrobe.live(prog(level=4))["styles"]["eyes"] == "pixel"        # but what is worn first is pixel art


def test_the_notch_starts_plain_and_only_unlocked_decoration_can_be_chosen():
    st = wardrobe.state(prog(level=1))
    assert st["notch"] == {"plate": "black", "frame": "hairline", "shape": "round", "ledge": "plain", "lamp": "dot", "pins": []}
    ok, why = wardrobe.set_notch("frame", "glow", prog(level=1))
    assert not ok and "level 2" in why
    assert wardrobe.set_notch("frame", "glow", prog(level=2)) == (True, "")
    assert wardrobe.live(prog(level=2))["notch"]["frame"] == "glow"
    assert wardrobe.live(prog(level=1))["notch"]["frame"] == "hairline"        # never reported if it is not earned


def test_the_notch_holds_three_pins_and_a_pin_toggles_off():
    big = prog(level=9, tasks=100, sprints=20, habits=30, streak=30)
    for pin in ("star", "bolt", "leaf"):
        assert wardrobe.set_notch("pins", pin, big) == (True, "")
    ok, why = wardrobe.set_notch("pins", "flame", big)
    assert not ok and "3 pins" in why
    assert wardrobe.set_notch("pins", "bolt", big) == (True, "")
    assert wardrobe.live(big)["notch"]["pins"] == ["star", "leaf"]
    assert wardrobe.set_notch("nonsense", "x", big)[0] is False


def test_taking_everything_off_also_clears_the_notch():
    big = prog(level=9)
    wardrobe.set_notch("ledge", "gold", big)
    wardrobe.take_everything_off()
    assert wardrobe.live(big)["notch"]["ledge"] == "plain"


def test_the_app_window_starts_plain_and_only_unlocked_decoration_can_be_chosen():
    assert wardrobe.state(prog())["app"] == {"skin": "nocturne", "backdrop": "none", "cards": "hairline", "accent": "buddy", "scene": "plate"}
    ok, why = wardrobe.set_app("skin", "paper", prog(level=1))
    assert not ok and "level 2" in why
    assert wardrobe.set_app("skin", "paper", prog(level=2)) == (True, "")
    assert wardrobe.live(prog(level=2))["app"]["skin"] == "paper"
    assert wardrobe.live(prog(level=1))["app"]["skin"] == "nocturne"           # never reported if it is not earned
    assert wardrobe.set_app("nonsense", "x", prog(level=9))[0] is False
    wardrobe.take_everything_off()
    assert wardrobe.live(prog(level=9))["app"]["skin"] == "nocturne"


def test_each_character_keeps_its_own_wardrobe_and_the_live_feed_follows_the_chosen_buddy(monkeypatch):
    big = prog(level=9, tasks=100, sprints=20, habits=30, streak=30)
    assert wardrobe.wear("crown", "halo", big, "blink") == (True, "")
    assert wardrobe.wear("crown", "accretion", big, "void") == (True, "")
    assert wardrobe.live(big, "blink")["worn"]["crown"] == "halo"
    assert wardrobe.live(big, "void")["worn"]["crown"] == "accretion"
    assert wardrobe.wear("crown", "accretion", big, "blink")[0] is False      # an item belongs to one character
    ok, why = wardrobe.wear("crown", "thirdeye", prog(level=1), "oracle")
    assert not ok and "level 6" in why
    monkeypatch.setattr(wardrobe, "current_character", lambda: "void")
    assert wardrobe.live(big)["character"] == "void" and wardrobe.live(big)["worn"]["crown"] == "accretion"
    assert wardrobe.state(big)["slots"]["crown"][0]["id"] == "accretion"


def test_only_blinks_items_have_a_line_drawing():
    big = prog(level=9)
    wardrobe.wear("crown", "accretion", big, "void")
    ok, why = wardrobe.set_style("crown", "line", "void")
    assert not ok and "pixel art only" in why
    assert wardrobe.live(big, "void")["styles"]["crown"] == "pixel"
    assert wardrobe.state(big, "void")["slots"]["crown"][0]["line"] is False
    assert wardrobe.state(big, "blink")["slots"]["crown"][0]["line"] is True


def test_a_wardrobe_saved_before_each_buddy_had_its_own_becomes_blinks(tmp_path, monkeypatch):
    import json
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"worn": {"crown": "halo"}, "finish": "asis", "ink": "mint", "rev": 5, "colors": {"crown": "rose"}, "styles": {}}), encoding="utf-8")
    monkeypatch.setenv("MBUDDY_WARDROBE_PATH", str(path))
    wardrobe.reset()
    s = wardrobe.live(prog(level=9), "blink")
    assert s["worn"]["crown"] == "halo" and s["colors"]["crown"] == "rose" and s["ink"] == "mint"
    assert wardrobe.live(prog(level=9), "void")["worn"]["crown"] == ""


def test_a_locked_decoration_can_be_tried_on_without_unlocking_or_saving_anything(monkeypatch):
    low = prog(level=1)
    assert wardrobe.live(low)["worn"]["crown"] == ""
    assert wardrobe.preview("wear", "crown", "halo", "blink") == (True, "")                  # Halo needs level 6
    assert wardrobe.preview("notch", "ledge", "gold") == (True, "")
    assert wardrobe.preview("notch", "pins", "star") == (True, "")
    assert wardrobe.preview("app", "skin", "paper") == (True, "")
    live = wardrobe.live(low, "blink")
    assert live["worn"]["crown"] == "halo" and live["notch"]["ledge"] == "gold" and live["notch"]["pins"] == ["star"] and live["app"]["skin"] == "paper"
    assert live["preview"]["left"] > 0
    ok, why = wardrobe.wear("crown", "halo", low, "blink")                                   # trying it on does not make it wearable
    assert not ok and "level 6" in why
    assert wardrobe.state(low, "blink")["slots"]["crown"][1]["unlocked"] is False
    stored = wardrobe._load()                                                                 # and nothing was written
    assert stored["chars"]["blink"]["worn"]["crown"] == "" and stored["notch"]["ledge"] == "plain" and stored["app"]["skin"] == "nocturne"
    names = wardrobe.state(low, "blink")["preview"]["items"]
    assert any("Halo" in n["what"] and "level 6" in n["need"] for n in names)
    wardrobe.stop_preview()
    after = wardrobe.live(low, "blink")
    assert after["worn"]["crown"] == "" and after["notch"]["ledge"] == "plain" and "preview" not in after


def test_a_preview_runs_out_by_itself_and_every_change_is_seen_by_the_windows(monkeypatch):
    real = wardrobe.time.time
    clock = {"t": real()}
    monkeypatch.setattr(wardrobe.time, "time", lambda: clock["t"])
    low = prog(level=1)
    before = wardrobe.live(low, "blink")["rev"]
    wardrobe.preview("wear", "crown", "halo", "blink")
    during = wardrobe.live(low, "blink")["rev"]
    assert during != before
    clock["t"] += wardrobe.PREVIEW_S - 1
    assert wardrobe.live(low, "blink")["worn"]["crown"] == "halo"
    clock["t"] += 2
    gone = wardrobe.live(low, "blink")
    assert gone["worn"]["crown"] == "" and gone["rev"] != during and "preview" not in gone       # what a window last saw is not what it sees now: it repaints


def test_the_preview_refuses_what_does_not_exist_and_follows_the_buddy():
    assert wardrobe.preview("wear", "crown", "nonsense", "blink")[0] is False
    assert wardrobe.preview("wear", "crown", "halo", "void")[0] is False                      # Halo is Blink's, not Void's
    assert wardrobe.preview("notch", "frame", "nope")[0] is False and wardrobe.preview("app", "x", "y")[0] is False
    assert wardrobe.preview("wear", "crown", "accretion", "void") == (True, "")
    assert wardrobe.live(prog(level=1), "void")["worn"]["crown"] == "accretion"
    assert wardrobe.live(prog(level=1), "blink")["worn"]["crown"] == ""                       # it is Void that is being dressed
