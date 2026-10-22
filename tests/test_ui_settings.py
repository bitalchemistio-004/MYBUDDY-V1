"""The app's settings store (docs/PHASE3_BUILD_PLAN.md 3.1)."""
import json
from pathlib import Path

import pytest

from core import ui_settings as s


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    s.reset_cache()
    yield tmp_path / "app_settings.json"
    s.reset_cache()


def test_defaults_wear_the_buddys_look_and_everything_is_on():
    d = s.all_settings()
    assert d["theme"] == "character" and d["character"] in s.CHARACTERS and "clay" in s.THEMES
    assert d["speak_replies"] and d["internet_search"] and not d["onboarding_done"]
    assert all(v == "act" for v in d["tool_permissions"].values())


def test_update_persists_and_survives_a_reload(isolated):
    s.update({"character": "void", "theme": "character", "emotion_strength": 80})
    s.reset_cache()
    d = s.all_settings()
    assert (d["character"], d["theme"], d["emotion_strength"]) == ("void", "character", 80)
    assert json.loads(isolated.read_text())["character"] == "void"


def test_a_character_saved_from_an_older_cast_becomes_the_default(isolated):
    """The cast changed; a settings file that names a character that no longer
    exists must open as the default one, and keep every other saved setting."""
    isolated.write_text(json.dumps({"character": "mochi", "buddy_size": "large"}), encoding="utf-8")
    s.reset_cache()
    d = s.all_settings()
    assert d["character"] == s.CHARACTERS[0] == s.DEFAULTS["character"]
    assert d["buddy_size"] == "large"


def test_the_island_style_is_gone_and_a_saved_one_opens_as_the_floating_default(isolated):
    # the default is the corner: MBUDDY never assumes the screen has a notch
    assert "island" not in s.POPUP_STYLES and s.DEFAULTS["popup_style"] == "corner"
    with pytest.raises(s.SettingsError):
        s.update({"popup_style": "island"})
    isolated.write_text(json.dumps({"popup_style": "island", "buddy_side": "left"}), encoding="utf-8")
    s.reset_cache()
    d = s.all_settings()
    assert (d["popup_style"], d["buddy_side"]) == (s.DEFAULTS["popup_style"], "left")


def test_the_dragged_place_is_kept_and_only_whole_pixels_for_a_real_style_are_taken():
    s.update({"buddy_position": {"x": 1200, "y": -40, "for": "corner:right"}})
    assert s.get("buddy_position") == {"x": 1200, "y": -40, "for": "corner:right"}
    for bad in ({"x": 1.5, "y": 2, "for": "corner:right"}, {"x": 1, "y": 2}, {"x": 1, "y": 2, "for": "island:right"},
                {"x": True, "y": 2, "for": "edge:left"}, {"x": 1, "y": 2, "for": "edge:left", "z": 3}, "top"):
        with pytest.raises(s.SettingsError):
            s.update({"buddy_position": bad})
    s.update({"buddy_position": {}})
    assert s.get("buddy_position") == {}
    assert s.DEFAULTS["buddy_effects"] is True


def test_every_character_has_its_drawn_clips():
    """The picker lists CHARACTERS; each one needs its art in the app."""
    chars = Path(__file__).resolve().parent.parent / "shell" / "ui" / "assets" / "buddy" / "chars"
    for name in s.CHARACTERS:
        clips = json.loads((chars / name / "clips.json").read_text(encoding="utf-8"))["clips"]
        assert "idle" in clips and clips["idle"]["loop"], name
        assert all((chars / name / f"{clip}.webp").is_file() for clip in clips), name
        assert (chars / name / "still.webp").is_file(), name
    assert sorted(p.name for p in chars.iterdir()) == sorted(s.CHARACTERS), "no art left over from another cast"


@pytest.mark.parametrize("change", [
    {"character": "toffee"}, {"theme": "neon"}, {"emotion_strength": 150}, {"speak_replies": "yes"},
    {"no_such_setting": 1}, {"tool_permissions": {"volume": "maybe"}}, {"voice": "  "},
])
def test_invalid_values_are_refused_and_nothing_is_stored(change):
    with pytest.raises(s.SettingsError):
        s.update(change)
    assert s.all_settings() == s.DEFAULTS


def test_one_bad_value_rejects_the_whole_change():
    with pytest.raises(s.SettingsError):
        s.update({"character": "void", "theme": "neon"})
    assert s.get("character") == s.DEFAULTS["character"]


def test_tool_permissions_merge_and_gate():
    s.update({"tool_permissions": {"search": "off"}})
    assert not s.tool_allowed("search") and s.tool_allowed("volume")
    assert s.get("tool_permissions")["volume"] == "act"


def test_a_corrupt_saved_value_falls_back_to_its_default(isolated):
    isolated.write_text(json.dumps({"character": "pip", "theme": "character"}))
    s.reset_cache()
    assert s.get("character") == s.DEFAULTS["character"] and s.get("theme") == "character"


def test_a_change_made_by_another_process_is_not_reverted(tmp_path, monkeypatch):
    # Live 2026-09-27: one process switched the microphone on; another, holding
    # an older copy, saved a different setting and wrote the old "off" back.
    import json, os, time
    from core import ui_settings
    path = tmp_path / "app_settings.json"
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(path))
    ui_settings.reset_cache()
    ui_settings.update({"microphone_on": False})          # this process's copy: off
    data = json.loads(path.read_text(encoding="utf-8"))
    data["microphone_on"] = True                            # another process: on
    path.write_text(json.dumps(data), encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))                          # a newer file
    ui_settings.update({"show_buddy": False})               # an unrelated change here
    assert json.loads(path.read_text(encoding="utf-8"))["microphone_on"] is True
    assert ui_settings.get("microphone_on") is True
    ui_settings.reset_cache()


def test_working_on_the_phone_silences_the_pc_without_touching_the_stored_switch(tmp_path, monkeypatch):
    from core import ui_settings as u
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    u._store.reset_cache()
    u.update({"speak_replies": True})
    assert u.get("speak_replies") is True
    u.update({"work_on": "phone"})
    assert u.get("speak_replies") is False and u.all_settings()["speak_replies"] is True
    u.update({"work_on": "pc"})
    assert u.get("speak_replies") is True


def test_the_notch_settings_are_checked_and_merged(isolated):
    d = s.DEFAULTS["notch"]
    assert d["glow"] == "mood" and d["micro"] is True and set(d["buttons"]) == set(s.NOTCH_ACTIONS)
    out = s.update({"notch": {"glow": "off", "length": "long", "glow_strength": 80.4, "colour": "#FF7A2F", "buttons": ["music", "timer", "timer"]}})["notch"]
    assert (out["glow"], out["length"], out["glow_strength"], out["colour"]) == ("off", "long", 80, "#ff7a2f")
    assert out["buttons"] == ["timer", "music"] and out["thickness"] == "thin"         # the rest is kept
    for bad in ({"glow": "rainbow"}, {"clock": "yes"}, {"glow_strength": 101}, {"gap": 40}, {"colour": "red"}, {"buttons": ["format_disk"]}, {"sparkle": True}):
        with pytest.raises(s.SettingsError):
            s.update({"notch": bad})


def test_where_hearing_and_voice_run_is_a_checked_choice_that_the_models_follow(isolated, monkeypatch):
    assert s.DEFAULTS["speech_device"] == "auto"
    with pytest.raises(s.SettingsError):
        s.update({"speech_device": "tpu"})
    from stt import local_whisper
    monkeypatch.delenv("STT_DEVICE", raising=False)
    assert local_whisper._speech_on_cpu() is False
    s.update({"speech_device": "cpu"})
    assert local_whisper._speech_on_cpu() is True                      # hearing and voice then load on the processor


def test_the_closed_notch_is_compact_by_default():
    assert "compact" in s.NOTCH_CHOICES["length"]
    assert s.DEFAULTS["notch"]["length"] == "compact"
