"""The user's own profile text: validation and the prompt block (S83)."""
import pytest

from brain.user_profile import profile_text
from core import ui_settings as s


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    s.reset_cache()
    yield
    s.reset_cache()


def test_both_default_to_empty_and_give_no_block():
    assert s.get("about_me") == "" and s.get("companion_style") == ""
    assert profile_text() == ""


def test_text_is_trimmed_and_keeps_line_breaks():
    d = s.update({"about_me": "  I'm Sam.\r\nI build robots.\x07  ", "companion_style": "\n Be blunt. \n"})
    assert d["about_me"] == "I'm Sam.\nI build robots."
    assert d["companion_style"] == "Be blunt."


def test_empty_is_allowed_and_clears_it():
    s.update({"about_me": "I'm Sam."})
    assert s.update({"about_me": "   "})["about_me"] == ""


@pytest.mark.parametrize("change", [
    {"about_me": "x" * (s.PROFILE_MAX + 1)}, {"companion_style": "x" * 5000},
    {"about_me": 42}, {"companion_style": None}, {"about_me": ["a"]},
])
def test_too_long_or_not_text_is_refused(change):
    with pytest.raises(s.SettingsError):
        s.update(change)
    assert s.get("about_me") == "" and s.get("companion_style") == ""


def test_exactly_the_limit_is_accepted():
    assert len(s.update({"about_me": "y" * s.PROFILE_MAX})["about_me"]) == s.PROFILE_MAX


def test_profile_text_uses_both_parts():
    s.update({"about_me": "I'm Sam, a nurse on night shifts.", "companion_style": "Keep it short and kind."})
    text = profile_text()
    assert text.startswith("ABOUT THE USER (written by them):\nI'm Sam, a nurse on night shifts.")
    assert "HOW THEY WANT YOU TO BE WITH THEM" in text and "unsafe or dishonest" in text
    assert text.endswith("Keep it short and kind.")


def test_profile_text_with_only_one_part():
    s.update({"companion_style": "Tease me a little."})
    text = profile_text()
    assert "ABOUT THE USER" not in text and text.endswith("Tease me a little.")
    s.update({"companion_style": "", "about_me": "I'm Sam."})
    assert profile_text() == "ABOUT THE USER (written by them):\nI'm Sam."


def test_the_suggestion_is_a_placeholder_not_a_value():
    assert 0 < len(s.COMPANION_STYLE_SUGGESTION) <= s.PROFILE_MAX
    assert s.DEFAULTS["companion_style"] == ""
    s.update({"companion_style": s.COMPANION_STYLE_SUGGESTION})  # valid if the user adopts it
