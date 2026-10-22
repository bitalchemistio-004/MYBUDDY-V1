"""Each app setting really changes behaviour (docs/PHASE3_BUILD_PLAN.md 3.1)."""
import pytest

from core import ui_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    ui_settings.reset_cache()
    yield
    ui_settings.reset_cache()


def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_switched_off_tools_are_not_offered_to_the_model():
    from brain.agent import allowed_tools
    assert {"adjust_volume", "web_search", "remember", "set_timer"} <= _names(allowed_tools())
    ui_settings.update({"tool_permissions": {"volume": "off", "memory": "off"}})
    names = _names(allowed_tools())
    assert "adjust_volume" not in names and "remember" not in names and "set_timer" in names


def test_internet_search_off_removes_web_search():
    from brain.agent import allowed_tools, tool_switched_off
    ui_settings.update({"internet_search": False})
    assert "web_search" not in _names(allowed_tools()) and tool_switched_off("web_search")


def test_a_switched_off_tool_is_refused_honestly_even_if_called():
    from brain.agent import execute_calls
    from brain.tools import ToolFailure
    ui_settings.update({"tool_permissions": {"timers": "off"}})
    (name, _args, result), = execute_calls([{"name": "set_timer", "args": {"minutes": 1}}], None)
    assert isinstance(result, ToolFailure) and "switched off" in str(result)


def test_the_chat_model_comes_from_settings():
    from brain.agent import _chat_model
    ui_settings.update({"llm_model": "llama3.2:3b"})
    assert _chat_model() == "llama3.2:3b"


def test_feeling_in_the_voice_scales_pitch_and_blend():
    from tts.speaker import emotion_amount, emotion_pitch_factor
    from tts.local_tts import voice_for_emotion
    assert emotion_amount() == 1.0
    happy_default = emotion_pitch_factor("Happy")
    ui_settings.update({"emotion_strength": 0})
    assert emotion_amount() == 0 and emotion_pitch_factor("Happy") == 1.0
    assert voice_for_emotion("Happy", "af_heart", 0) == "af_heart"  # plain voice, no blend
    ui_settings.update({"emotion_strength": 100})
    assert emotion_pitch_factor("Happy") > happy_default


def test_weather_uses_the_home_location_when_no_place_is_named(monkeypatch):
    import brain.tools as tools
    seen = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"current_condition": [{}], "nearest_area": [{}]}

    def fake_get(url, **kw):
        seen["url"] = url
        return Resp()

    monkeypatch.setattr(tools.requests, "get", fake_get)
    ui_settings.update({"home_location": "Casablanca"})
    tools.execute_weather_tool("")
    assert "wttr.in/Casablanca" in seen["url"]


def test_the_users_name_replaces_the_placeholder_in_prompts():
    from brain.agent import build_messages
    from core.ui_settings import personalize
    assert personalize("USER_NAME's sister is Lina.") == "The user's sister is Lina."  # no name built in: until it is told, "the user"
    ui_settings.update({"user_name": "Sara"})
    assert personalize("USER_NAME's sister is Lina. Hi USER_NAME!") == "Sara's sister is Lina. Hi Sara!"
    assert "USER_NAME" not in build_messages("hi", [])[0]["content"] and "Sara" in build_messages("hi", [])[0]["content"]


def test_buddy_settings_validate():
    ui_settings.update({"show_buddy": False, "buddy_size": "large", "buddy_side": "left", "microphone": "USB Mic"})
    for bad in ({"buddy_size": "huge"}, {"buddy_side": "top"}, {"user_name": "R2-D2 <script>"}, {"user_name": ""}):
        with pytest.raises(ui_settings.SettingsError):
            ui_settings.update(bad)


def test_local_services_are_reached_by_ipv4_address():
    # "localhost" cost ~2 s per model call on Windows (IPv6 tried first).
    from core.config import prefer_ipv4_loopback
    env = {"OLLAMA_BASE_URL": "http://localhost:11434/v1", "LLM_LOCAL_ENDPOINT": "http://localhost:11434",
           "OTHER": "http://localhost:1"}
    prefer_ipv4_loopback(env)
    assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert env["LLM_LOCAL_ENDPOINT"] == "http://127.0.0.1:11434"
    assert env["OTHER"] == "http://localhost:1"  # only the local AI endpoints


def test_microphone_off_means_nothing_is_heard(tmp_path, monkeypatch):
    import asyncio
    import numpy as np
    from api.websocket_server import MBUDDYSession
    from core import ui_settings

    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    assert ui_settings.get("microphone_on") is True  # on by default

    heard = []

    class FakeSession:
        def feed_chunk(self, chunk):
            heard.append(len(chunk))
            return False
        audio = type("A", (), {"audio_buffer": []})()
        finalize_lock = asyncio.Lock()

    chunk = (np.ones(256, dtype=np.int16) * 1000).tobytes()
    asyncio.run(MBUDDYSession.handle_audio(FakeSession(), chunk))
    assert heard == [256]
    ui_settings.update({"microphone_on": False})
    asyncio.run(MBUDDYSession.handle_audio(FakeSession(), chunk))
    assert heard == [256]  # switched off: the chunk never reached the listener
    ui_settings.reset_cache()


def test_the_pc_status_says_what_is_playing(monkeypatch):
    from brain import pc_windows as pw
    monkeypatch.setattr(pw, "list_windows", lambda include_own=False: [pw.Window(1, "YouTube Music - Google Chrome", "chrome", True, False)])
    monkeypatch.setattr(pw, "system_state", lambda: "CPU 5%")
    monkeypatch.setattr(pw, "playing_audio", lambda: [("chrome", False)])
    assert "Playing sound: chrome (YouTube Music - Google Chrome)" in pw.pc_status_text()
    monkeypatch.setattr(pw, "playing_audio", lambda: [])
    assert "Playing sound: nothing right now." in pw.pc_status_text()
