import asyncio
import sqlite3

import numpy as np


def test_device_protocol_preserves_esp32_tags():
    from api.device_protocol import DeviceProtocol

    assert DeviceProtocol.speaking("Happy", "hello") == "[SPEAKING] [STATE:Happy] hello"
    assert DeviceProtocol.state_quiet("Sleepy") == "[STATE:Sleepy][QUIET]"
    assert DeviceProtocol.heard("ready") == "[HEARD] ready"
    assert DeviceProtocol.action("Joy Stick!", 1200) == "[ACT:joystick:1200]"
    assert DeviceProtocol.sfx("annoyed-click") == "[SFX:annoyedclick]"


def test_parse_hello_accepts_well_formed_envelope():
    import json
    from api.device_protocol import parse_hello

    text = json.dumps({
        "version": 1, "type": "hello",
        "device_id": "mbuddy-body-01", "session_id": "abc123",
        "firmware_version": "5.2.0", "auth_token": "secret",
        "capabilities": {"mic": True, "speaker": True},
    })
    hello = parse_hello(text)
    assert hello is not None
    assert hello["device_id"] == "mbuddy-body-01"
    assert hello["capabilities"] == {"mic": True, "speaker": True}


def test_parse_hello_rejects_missing_field():
    import json
    from api.device_protocol import parse_hello

    text = json.dumps({
        "version": 1, "type": "hello",
        "device_id": "mbuddy-body-01", "session_id": "abc123",
        "firmware_version": "5.2.0",  # auth_token missing
    })
    assert parse_hello(text) is None


def test_parse_hello_rejects_wrong_type_and_garbage():
    import json
    from api.device_protocol import parse_hello

    assert parse_hello(json.dumps({"type": "not_hello"})) is None
    assert parse_hello("not json at all") is None
    assert parse_hello("[BARGE]") is None  # an ordinary bracket-tag message is not HELLO


def test_hello_ack_and_reject_shape():
    import json
    from api.device_protocol import hello_ack, hello_reject

    ack = json.loads(hello_ack("sess-1"))
    assert ack == {"version": 1, "type": "hello_ack", "accepted": True, "session_id": "sess-1"}

    rej = json.loads(hello_reject("unauthorized"))
    assert rej == {"version": 1, "type": "hello_reject", "accepted": False, "reason": "unauthorized"}


def test_validate_device_token(monkeypatch):
    import core.config as config
    from api.auth import validate_device_token

    monkeypatch.setattr(config, "MBUDDY_DEVICE_TOKEN", "correct-secret")
    assert validate_device_token("correct-secret") is True
    assert validate_device_token("wrong-secret") is False
    assert validate_device_token("") is False
    assert validate_device_token(None) is False


def test_validate_device_token_rejects_everything_when_unconfigured(monkeypatch):
    """An empty configured secret must never make every HELLO 'match' by comparing "" == ""."""
    import core.config as config
    from api.auth import validate_device_token

    monkeypatch.setattr(config, "MBUDDY_DEVICE_TOKEN", "")
    assert validate_device_token("") is False
    assert validate_device_token("anything") is False


def test_validate_device_token_default_allowed_only_off_tunnel(monkeypatch):
    """
    The shipped default token must keep working for local/LAN development
    (tunnel inactive), but once a tunnel makes /ws reachable from outside the
    LAN, authenticating against a value published in this repo's own source
    would be no better than no auth at all — every HELLO must be refused
    until a real secret is configured, even one that correctly quotes the
    (now-unsafe) default.
    """
    import core.config as config
    from api.auth import validate_device_token

    monkeypatch.setattr(config, "MBUDDY_DEVICE_TOKEN", config.MBUDDY_DEVICE_TOKEN_DEFAULT)

    monkeypatch.setenv("MBUDDY_TUNNEL_ACTIVE", "0")
    assert validate_device_token(config.MBUDDY_DEVICE_TOKEN_DEFAULT) is True

    monkeypatch.setenv("MBUDDY_TUNNEL_ACTIVE", "1")
    assert validate_device_token(config.MBUDDY_DEVICE_TOKEN_DEFAULT) is False


def test_validate_device_token_non_default_still_works_with_tunnel_active(monkeypatch):
    """A real, non-default secret must still authenticate normally even with a tunnel active."""
    import core.config as config
    from api.auth import validate_device_token

    monkeypatch.setattr(config, "MBUDDY_DEVICE_TOKEN", "a-real-rotated-secret")
    monkeypatch.setenv("MBUDDY_TUNNEL_ACTIVE", "1")
    assert validate_device_token("a-real-rotated-secret") is True
    assert validate_device_token("wrong") is False


def test_tunnel_manager_sets_and_clears_tunnel_active_env(monkeypatch):
    """api/tunnel_manager.py must flip MBUDDY_TUNNEL_ACTIVE automatically, not rely on an
    operator remembering to set it by hand — otherwise the device-token production gate
    above and the dashboard's require_auth() bypass logic never actually engage."""
    import os
    import subprocess
    from api.tunnel_manager import TunnelManager

    class DummyProcess:
        def __init__(self):
            self.stdout = iter([])  # _reader() thread exits immediately, nothing to read
        def poll(self):
            return None
        def terminate(self):
            pass
        def wait(self, timeout=None):
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: DummyProcess())
    mgr = TunnelManager()
    monkeypatch.setattr(mgr, "_available_cache", True, raising=False)

    monkeypatch.delenv("MBUDDY_TUNNEL_ACTIVE", raising=False)
    mgr.start()
    assert os.environ.get("MBUDDY_TUNNEL_ACTIVE") == "1"

    mgr.stop()
    assert os.environ.get("MBUDDY_TUNNEL_ACTIVE") == "0"


def test_hello_timeout_rejects_without_online_and_leaves_state_unaffected(monkeypatch):
    """
    connect -> send no HELLO -> timeout -> connection closed -> device never
    reaches ONLINE -> runtime_status left exactly as it was found.

    Asserts "state after == state before" rather than "not ONLINE", so this
    holds regardless of what earlier tests left the shared runtime_status
    singleton at — the point is that THIS attempt changes nothing, not what
    the starting value happened to be.
    """
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient

    import api.websocket_server as ws_module
    from core.status import runtime_status

    monkeypatch.setattr(ws_module, "HELLO_TIMEOUT_S", 0.3)  # real default is 5s; too slow for a unit test

    app = web.Application()
    app.router.add_get("/ws", ws_module.websocket_handler)

    async def run():
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            before = runtime_status.device_conn_state
            ws = await client.ws_connect("/ws")
            try:
                # Send nothing — wait for the server's own timeout to close us.
                msg = await ws.receive(timeout=2.0)
                assert msg.type == web.WSMsgType.TEXT
                import json
                payload = json.loads(msg.data)
                assert payload["type"] == "hello_reject"
                assert payload["reason"] == "hello_timeout"

                closed = await ws.receive(timeout=2.0)
                assert closed.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSED)
            finally:
                await ws.close()
            assert runtime_status.device_conn_state == before
        finally:
            await client.close()

    asyncio.run(run())


def test_runtime_status_snapshot_contains_dashboard_fields():
    from core.status import runtime_status

    runtime_status.device_connect(remote="test")
    runtime_status.update(awake=True, emotion="Focused")
    snap = runtime_status.snapshot()

    assert snap["device_connected"] is True
    assert snap["awake"] is True
    assert snap["emotion"] == "Focused"
    assert "uptime_s" in snap


def test_device_conn_state_starts_registered_and_not_connected():
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    assert status.device_conn_state == DeviceConnState.REGISTERED.value
    assert status.device_connected is False


def test_device_silence_thresholds_stay_ordered_and_clear_keepalive():
    """
    Regression guard for the Phase 1A evidence-based tuning: DEGRADED must
    trigger before OFFLINE, and the soft-silence threshold must stay above
    the firmware's real 30 s keep-alive interval (MBUDDY.ino, ~line 1605) so
    an idle-but-healthy device is never flagged DEGRADED just for going as
    quiet as the firmware's own design allows.
    """
    from core.status import RuntimeStatus

    status = RuntimeStatus()
    FIRMWARE_KEEPALIVE_INTERVAL_S = 30.0
    assert status.DEVICE_SOFT_SILENCE_S > FIRMWARE_KEEPALIVE_INTERVAL_S
    assert status.DEVICE_SOFT_SILENCE_S < status.DEVICE_SILENCE_TIMEOUT_S


def test_device_connect_moves_to_online():
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    status.device_connect(remote="1.2.3.4")
    assert status.device_conn_state == DeviceConnState.ONLINE.value
    assert status.device_connected is True
    assert status.remote == "1.2.3.4"
    assert status.device_connected_at is not None
    assert status.device_disconnected_at is None


def test_device_sweep_demotes_silent_online_device_to_degraded():
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    status.device_connect()
    status.last_ws_seen_at -= status.DEVICE_SOFT_SILENCE_S + 1  # simulate elapsed silence
    status.device_sweep()
    assert status.device_conn_state == DeviceConnState.DEGRADED.value
    # Still counted as connected — this is a soft warning, not a disconnect.
    assert status.device_connected is True


def test_device_heartbeat_recovers_degraded_to_online():
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    status.device_connect()
    status.device_conn_state = DeviceConnState.DEGRADED.value
    status.device_heartbeat()
    assert status.device_conn_state == DeviceConnState.ONLINE.value


def test_device_disconnect_moves_to_reconnecting_not_offline():
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    status.device_connect()
    status.device_disconnect()
    assert status.device_conn_state == DeviceConnState.RECONNECTING.value
    # Never falsely reads as connected once disconnected — the bug this replaces.
    assert status.device_connected is False
    assert status.device_disconnected_at is not None


def test_device_sweep_gives_up_on_reconnecting_device_after_timeout():
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    status.device_connect()
    status.device_disconnect()
    status.device_disconnected_at -= status.DEVICE_SILENCE_TIMEOUT_S + 1
    status.device_sweep()
    assert status.device_conn_state == DeviceConnState.OFFLINE.value
    assert status.device_connected is False


def test_device_reconnect_after_offline_recovers_to_online():
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    status.device_connect()
    status.device_disconnect()
    status.device_conn_state = DeviceConnState.OFFLINE.value
    status.device_connect()
    assert status.device_conn_state == DeviceConnState.ONLINE.value
    assert status.device_connected is True


def test_update_with_device_connected_is_a_safe_noop():
    """A leftover `runtime_status.update(device_connected=...)` call site (there is
    none left in the codebase, but this guards against a regression) must not crash
    now that device_connected is a read-only derived property."""
    from core.status import RuntimeStatus, DeviceConnState

    status = RuntimeStatus()
    status.device_connect()
    status.update(device_connected=False, awake=True)  # must not raise
    assert status.device_conn_state == DeviceConnState.ONLINE.value  # unchanged by the no-op
    assert status.awake is True  # other kwargs in the same call still applied


def test_status_handler_builds_boot_world_model_without_device():
    from api.status import status_handler
    from core.status import RuntimeStatus
    import api.status as status_module

    original = status_module.runtime_status
    status_module.runtime_status = RuntimeStatus()

    class DummyRequest:
        query = {}  # status_handler reads ?full=1
        remote = "127.0.0.1"  # status is local-only

    try:
        response = asyncio.run(status_handler(DummyRequest()))
        assert response.status == 200
        snap = status_module.runtime_status.snapshot()
        assert snap["world_model"]["current_time"]
        assert "clock" in snap["world_model"]["available_tools"]
        # The calculator is implemented (brain/tools.py) and registered as available.
        assert "calculator" in snap["world_model"]["available_tools"]
        assert not any(item["name"] == "calculator" for item in snap["disconnected_features"])
    finally:
        status_module.runtime_status = original


def test_sqlite_memory_store_round_trip(tmp_path):
    from memory.sqlite_store import SQLiteMemoryStore, default_memory

    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    mem = default_memory()
    mem["core_facts"].append("Sam is building MBUDDY.")
    mem["conversation_log"].append({
        "time": "2026-05-26 09:00",
        "user": "hey buddy",
        "mbuddy": "hey",
        "lang": "en",
    })

    store.save(mem)
    store.save(mem)
    loaded = store.load()

    assert loaded["core_facts"] == ["Sam is building MBUDDY."]
    assert loaded["conversation_log"][0]["user"] == "hey buddy"
    with sqlite3.connect(tmp_path / "memory.sqlite3") as conn:
        count = conn.execute("SELECT COUNT(*) FROM conversation_log").fetchone()[0]
    assert count == 1


def test_audio_session_barge_detection():
    from audio.session_state import AudioSessionState

    state = AudioSessionState()
    normal = ((np.arange(256) % 2 * 2 - 1).astype(np.int16) * 400).tobytes()
    loud = ((np.arange(256) % 2 * 2 - 1).astype(np.int16) * 1400).tobytes()

    for _ in range(10):
        assert state.note_loud_chunk(normal) is False
    assert state.note_loud_chunk(loud) is False
    assert state.note_loud_chunk(loud) is False
    assert state.note_loud_chunk(loud) is False
    assert state.note_loud_chunk(loud) is False
    assert state.note_loud_chunk(loud) is True
    assert state.last_barge_rms >= state.last_barge_gate


def test_fallback_replies_keep_emotion_tag():
    from brain.fallbacks import fallback_reply

    assert fallback_reply("llm_timeout", "Focused").startswith("[FOCUSED]")
    assert "lagged" in fallback_reply("llm_timeout", "Focused")


def test_llm_bridge_forwards_requested_model(monkeypatch):
    from brain import llm_bridge

    calls = []

    class DummyAdapter:
        async def stream_chat(self, messages, max_tokens, temperature, model=None):
            calls.append({
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "model": model,
            })
            yield "ok"

    monkeypatch.setattr(llm_bridge, "get_available_providers", lambda: ["groq"])
    monkeypatch.setattr(llm_bridge, "get_brain_adapter", lambda name: DummyAdapter())

    res = asyncio.run(
        llm_bridge.llm_chat_create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "utility"}],
            max_tokens=12,
            temperature=0.2,
        )
    )

    assert res.choices[0].message.content == "ok"
    assert calls == [{
        "messages": [{"role": "user", "content": "utility"}],
        "max_tokens": 12,
        "temperature": 0.2,
        "model": "llama-3.1-8b-instant",
    }]


def test_interrupted_inner_thought_enters_memory_context():
    from brain.memory_context import build_tiered_memory_context

    ctx = build_tiered_memory_context({
        "inner_log": ["I was thinking about the build."],
        "interrupted_inner_thought": "I should remember where that build idea was going.",
    })

    assert "PRIVATE THOUGHT THAT WAS INTERRUPTED" in ctx
    assert "where that build idea was going" in ctx


def test_inner_thought_thread_enters_memory_context():
    from brain.memory_context import build_tiered_memory_context

    ctx = build_tiered_memory_context({
        "inner_log": [
            "I wondered whether Sam was chasing too many systems at once.",
            "The memory of the voice pipeline made me think about patience.",
        ],
        "inner_state": {
            "mood": "Focused",
            "energy": 72.0,
            "identity_vector": "obsession: voice becoming smoother",
            "narrative_thread": ["thread one", "thread two"],
        },
    })

    assert "YOUR RECENT PRIVATE THOUGHT THREAD" in ctx
    assert "voice pipeline" in ctx
    assert "CURRENT INNER STATE SNAPSHOT" in ctx
    assert "obsession: voice becoming smoother" in ctx


def test_uncertain_exchange_is_not_memory_material():
    from memory.long_term import (
        _exchange_too_uncertain_for_memory,
        _looks_like_question,
        _user_text_can_contain_stable_fact,
    )

    assert _exchange_too_uncertain_for_memory(
        "bitalchemist speaks feroz",
        "Wait, you're saying Sam speaks Feroz, but I thought Sam spoke Arabic, what's going on here?",
    )
    assert _exchange_too_uncertain_for_memory(
        "tarif fairuz",
        "It seems like you're referring to Fairouz, but I'm not aware of any specific connection.",
    )
    assert _exchange_too_uncertain_for_memory(
        "ame speaks feroz",
        "You're speaking Arabic again, please translate or speak in English.",
    )
    assert not _exchange_too_uncertain_for_memory(
        "I speak Arabic and English",
        "Got it, Arabic and English.",
    )
    assert _exchange_too_uncertain_for_memory(
        "when is the world cup starting?",
        "I'm not sure, my training data does not have the latest schedule.",
    )
    assert _looks_like_question("when is the world cup starting?")
    assert not _user_text_can_contain_stable_fact("when is the world cup starting?")
    assert not _user_text_can_contain_stable_fact("why wouldn't you search about that?")
    assert _user_text_can_contain_stable_fact("I work on a robotics project")


def test_search_provider_can_be_disabled(monkeypatch):
    from utils.search_provider import get_search_provider

    monkeypatch.setenv("MBUDDY_ENABLE_WEB_SEARCH", "0")
    provider = get_search_provider()

    assert provider.search("latest news") == "Live search is disabled on this backend."


def test_search_provider_enabled_by_default(monkeypatch):
    from utils.search_provider import DdgsSearchProvider, DuckDuckGoHtmlSearchProvider, get_search_provider

    monkeypatch.delenv("MBUDDY_ENABLE_WEB_SEARCH", raising=False)
    monkeypatch.delenv("MBUDDY_SEARCH_PROVIDER", raising=False)

    assert isinstance(get_search_provider(), (DdgsSearchProvider, DuckDuckGoHtmlSearchProvider))


def test_google_search_provider_when_configured(monkeypatch):
    from utils.search_provider import GoogleCustomSearchProvider, get_search_provider

    monkeypatch.setenv("MBUDDY_ENABLE_WEB_SEARCH", "1")
    monkeypatch.setenv("MBUDDY_SEARCH_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_SEARCH_CX", "cx")

    assert isinstance(get_search_provider(), GoogleCustomSearchProvider)


def test_live_search_trigger_rules():
    from brain.llm_router import live_search_query, needs_live_search, search_query_for_user_text

    assert needs_live_search("what is the latest AI news")
    assert needs_live_search("look up the weather in Tripoli")
    assert needs_live_search("who is the president of France")
    assert needs_live_search("and it's who versus who")
    assert needs_live_search("when is the world cup starting?")
    assert not needs_live_search("what is photosynthesis")
    assert not needs_live_search("what date is it")
    assert not needs_live_search("what time is it")
    assert search_query_for_user_text("who is the president of France") == "who is the president of France"
    assert search_query_for_user_text("current president of France").startswith("current president of France 20")
    assert search_query_for_user_text("weather for Tripoli").startswith("weather for Tripoli 20")
    assert "fifa world cup" in search_query_for_user_text("when is the world cup starting?").lower()

    history = [
        {"role": "user", "content": "is the Champions League final today?"},
        {"role": "assistant", "content": "The Champions League final is today."},
    ]
    query = live_search_query("and it's who versus who", history)
    assert query is not None
    assert "champions league final" in query.lower()
    result_query = live_search_query("you should tell me the result of the finals", history)
    assert result_query is not None
    assert "uefa champions league final" in result_query.lower()
    assert "result" in result_query.lower()
    world_cup_history = [
        {"role": "user", "content": "when is the world cup starting?"},
        {"role": "assistant", "content": "I should have searched that."},
    ]
    followup_query = live_search_query("why wouldn't you search about that?", world_cup_history)
    assert followup_query is not None
    assert "fifa world cup" in followup_query.lower()
    assert live_search_query("what date is it", history) is None


def test_duckduckgo_provider_returns_structured_sources(monkeypatch):
    from utils.search_provider import DuckDuckGoHtmlSearchProvider

    html = """
    <html><body>
      <div class="result">
        <a class="result__a" href="/l/?kh=-1&uddg=https%3A%2F%2Fexample.com%2Fsource">Example Title</a>
        <a class="result__snippet">This is the supported fact.</a>
      </div>
    </body></html>
    """

    page_html = """
    <html><head><title>Example Source Page</title>
    <meta name="description" content="Official page description."></head>
    <body><h1>Current verified fact</h1><p>Detailed source evidence.</p></body></html>
    """

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def fake_get(url, headers, timeout):
        if "duckduckgo.com" in url:
            return FakeResponse(html)
        return FakeResponse(page_html)

    monkeypatch.setattr("utils.search_provider.requests.get", fake_get)

    result = DuckDuckGoHtmlSearchProvider().search("test query", max_results=1)

    assert "Fetched at:" in result
    assert "Provider: DuckDuckGo HTML" in result
    assert "Query: test query" in result
    assert "Example Title" in result
    assert "URL: https://example.com/source" in result
    assert "Snippet: This is the supported fact." in result
    assert "Page excerpt: Example Source Page Official page description." in result


def test_google_provider_returns_structured_sources(monkeypatch):
    from utils.search_provider import GoogleCustomSearchProvider

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "items": [
                    {
                        "title": "Official Result",
                        "link": "https://example.com/official",
                        "snippet": "Supported answer from Google.",
                        "pagemap": {"metatags": [{"description": "Official page summary."}]},
                    }
                ]
            }

    def fake_get(url, params, headers, timeout):
        assert "googleapis.com/customsearch" in url
        assert params["q"] == "test query"
        return FakeResponse()

    monkeypatch.setattr("utils.search_provider.requests.get", fake_get)

    result = GoogleCustomSearchProvider("key", "cx").search("test query", max_results=1)

    assert "Provider: Google Custom Search" in result
    assert "Query: test query" in result
    assert "Official Result" in result
    assert "URL: https://example.com/official" in result
    assert "Snippet: Supported answer from Google." in result
    assert "Page excerpt: Official page summary." in result


def test_internet_context_uses_strict_source_rules():
    from brain.llm_router import needs_live_search
    from brain.memory_context import build_conversation_system

    class DummyState:
        current = "Normal"
        energy = 85

    system = build_conversation_system(DummyState(), {}, "en")

    assert needs_live_search("current president of France")
    assert "Do not answer current internet facts from memory alone" in system
    assert "Never combine contradictory source claims" in system


def test_real_arabic_detection_utf8():
    from stt.language_detect import is_arabic

    assert is_arabic("مرحبا يا صديقي")


def test_arabic_language_approval_decline_utf8():
    from stt.language_policy import is_lang_approval, is_lang_decline

    assert is_lang_approval("أكيد")
    assert is_lang_approval("نعم")
    assert is_lang_decline("لا")
def test_tts_emotion_sets_kokoro_speed():
    # Replaces the edge-tts retry/fallback test: the cloud voice was removed
    # in the Phase 2 local rebuild. Kokoro has no pitch, so emotion keeps
    # only its pace from PROSODY_MAP.
    from tts import speaker

    assert speaker.emotion_speed("Normal") == 1.0
    assert abs(speaker.emotion_speed("Happy") - 1.10) < 1e-9
    assert abs(speaker.emotion_speed("Sleepy") - 0.70) < 1e-9
    assert speaker.emotion_speed("NotAnEmotion") == 1.0


def test_sfx_pcm_wire_format_is_short_mono_i16():
    from tts.sfx import SFX_NAMES, synthesize_sfx_pcm
    from tts.speaker import BYTES_PER_FRAME, TARGET_SR

    for name in SFX_NAMES:
        pcm = synthesize_sfx_pcm(name)
        assert pcm
        assert len(pcm) % BYTES_PER_FRAME == 0
        assert len(pcm) < TARGET_SR * BYTES_PER_FRAME
        samples = np.frombuffer(pcm, dtype=np.int16)
        assert samples.size > 0
        assert np.max(np.abs(samples.astype(np.int32))) > 0


def test_companion_director_idle_cues_are_stateful():
    from brain.companion_director import choose_inner_life_cue

    class DummyState:
        current = "Unimpressed"
        energy = 55

    cue = choose_inner_life_cue(DummyState(), {"inner_log": []})
    assert cue.action == "joystick"
    assert cue.sfx == "chiptune_loop"

    DummyState.current = "Annoyed"
    cue = choose_inner_life_cue(DummyState(), {})
    assert cue.action == "error_glitch"
    assert cue.sfx == "glitch_screech"


def test_cognitive_decision_allows_text_only_for_simple_turns():
    from brain.cognitive_state import build_cognitive_state, decide_behavior

    class DummyState:
        current = "Normal"
        energy = 72
        needs = {"rest": 20, "connection": 30, "novelty": 25, "competence": 50}

        def dominant_need(self):
            return "competence"

    class DummySession:
        auto_visuals_enabled = True
        is_awake = True
        ws = type("WS", (), {"closed": False})()

    cog = build_cognitive_state(DummyState(), session=DummySession())
    decision = decide_behavior(cog, event="thinking", text="hey")
    assert decision.output_mode == "text_only"
    assert decision.animation == ""
    assert decision.sfx == ""


def test_cognitive_decision_selects_reasoned_visuals_only_when_allowed():
    from brain.cognitive_state import build_cognitive_state, decide_behavior

    class DummyState:
        current = "Normal"
        energy = 72
        needs = {"rest": 20, "connection": 30, "novelty": 25, "competence": 50}

        def dominant_need(self):
            return "competence"

    class DummySession:
        auto_visuals_enabled = True
        is_awake = True
        ws = type("WS", (), {"closed": False})()

    cog = build_cognitive_state(DummyState(), session=DummySession())
    decision = decide_behavior(cog, event="thinking", text="how can we fix the dashboard behavior?")
    assert decision.animation == "data_weaver"
    assert "reasoning" in decision.reason.lower()

    DummySession.auto_visuals_enabled = False
    cog = build_cognitive_state(DummyState(), session=DummySession())
    decision = decide_behavior(cog, event="thinking", text="how can we fix the dashboard behavior?")
    assert decision.animation == ""
    assert "paused" in decision.reason.lower()


def test_world_model_and_registry_drive_cognitive_state():
    from brain.cognitive_state import build_cognitive_state, decide_behavior
    from brain.world_model import build_world_model

    class DummyState:
        current = "Normal"
        energy = 12
        needs = {"rest": 80, "connection": 20, "novelty": 10, "competence": 30}

        def dominant_need(self):
            return "rest"

    class DummySession:
        auto_visuals_enabled = True
        is_awake = True
        ws = type("WS", (), {"closed": False})()

    world = build_world_model(
        DummyState(),
        session=DummySession(),
        memory={"conversation_log": [{"user": "x"}], "facts": ["one"]},
        event="thinking",
        text="debug this issue",
    )
    cog = build_cognitive_state(DummyState(), session=DummySession(), world_model=world)
    decision = decide_behavior(cog, event="thinking", text="debug this issue")

    assert cog.worldModel["battery_level"] == 12
    assert "clock" in cog.availableTools
    assert "battery_charge" in cog.availableAnimations
    assert cog.memoryHealth["conversation_count"] == 1
    assert decision.animation == "battery_charge"
    assert decision.world_state_snapshot["day_period"] in {"morning", "afternoon", "evening", "night"}  # core/time_manager.get_day_period
    assert decision.animation_reason
    assert decision.reply_reason


def test_capability_registry_is_source_for_dashboard_lists():
    from api.device_control import VALID_ACTIONS, VALID_SFX
    from brain.capability_registry import ANIMATION_NAMES, SFX_NAMES, build_capability_registry

    registry = build_capability_registry(internet_available=False, hardware_connected=True, auto_visuals=True)

    assert VALID_ACTIONS == ANIMATION_NAMES
    assert VALID_SFX == SFX_NAMES
    assert "weather" not in registry.names("tool", usable_only=True)
    assert registry.get("weather").limitations


def test_conversation_prompt_contains_world_model_registry_and_authenticity():
    from brain.memory_context import build_conversation_system

    class DummyState:
        current = "Focused"
        energy = 70
        needs = {}

    prompt = build_conversation_system(
        DummyState(),
        {},
        "en",
        cognitive_state={"emotion": "Focused"},
        behavior_decision={"goal": "answer_user"},
        world_model={"internet_available": False, "current_time": "02:00:00"},
        capability_registry={"weather": {"available": False, "limitations": "Requires internet."}},
        authenticity_context={"repeated_openings": ["okay"], "guidance": "avoid repeats"},
    )

    assert "WORLD MODEL" in prompt
    assert "CAPABILITY REGISTRY" in prompt
    assert "RESPONSE AUTHENTICITY CONTEXT" in prompt
    assert "Never invent capabilities outside the registry" in prompt


def test_conversation_prompt_compacts_large_world_registry_and_memory():
    from brain.capability_registry import build_capability_registry
    from brain.memory_context import build_conversation_system
    from brain.world_model import build_world_model

    class DummyState:
        current = "Focused"
        energy = 70
        needs = {"competence": 80}

        def dominant_need(self):
            return "competence"

    memory = {
        "core_facts": [f"fact {i} " + ("x" * 500) for i in range(30)],
        "inner_log": [f"thought {i} " + ("y" * 600) for i in range(30)],
        "identity_snapshot": "z" * 5000,
    }
    registry = build_capability_registry(internet_available=True, hardware_connected=True, auto_visuals=True)
    world = build_world_model(DummyState(), memory=memory, event="conversation", text="debug this")

    prompt = build_conversation_system(
        DummyState(),
        memory,
        "en",
        cognitive_state={"emotion": "Focused", "worldModel": world.to_dict(), "capabilityRegistry": registry.snapshot()},
        behavior_decision={"goal": "answer_user", "animation_reason": "Reasoning work."},
        world_model=world.to_dict(),
        capability_registry=registry.snapshot(),
    )

    assert "WORLD MODEL" in prompt
    assert "CAPABILITY REGISTRY" in prompt
    # The date line alone varies by 3 characters (Monday vs Wednesday); the
    # bound is about compacting the 38k characters of input above.
    assert len(prompt) < 16100
    assert prompt.count("OLED animation:") == 0


def test_router_compacts_history_and_hides_provider_exception_names():
    from brain.llm_router import _compact_history_for_llm, _friendly_provider_fallback

    history = []
    for i in range(20):
        history.append({"role": "user", "content": "user " + ("x" * 900)})
        history.append({"role": "assistant", "content": "assistant " + ("y" * 900)})
    history.append({"role": "assistant", "content": "[MBUDDY]: My brain hit a wall — all providers unavailable right now. (APIStatusError)"})

    compact = _compact_history_for_llm(history)
    joined = " ".join(item["content"] for item in compact)
    fallback = _friendly_provider_fallback(Exception("APIStatusError 413 Request too large tokens per minute"), "en")

    assert len(joined) <= 3200
    assert "brain hit a wall" not in joined.lower()
    assert "APIStatusError" not in fallback
    assert "Request too large" not in fallback
    assert fallback.startswith("[WORRIED]")


def test_behavior_trace_requires_reason_and_tracks_recent_lists():
    from core.status import RuntimeStatus

    status = RuntimeStatus()
    trace = {
        "timestamp": "2026-06-03T00:00:00+00:00",
        "goal": "test_goal",
        "intent": "test_intent",
        "emotion": "Focused",
        "action": "data_weaver",
        "animation": "data_weaver",
        "sfx": "data_blip",
        "tool_used": "memory",
        "reason": "Test behavior has a reason.",
        "priority": "normal",
        "decision": {"reason": "Test behavior has a reason."},
        "competing_behaviors": [],
        "conflicts": [],
    }
    status.record_behavior_trace(trace)

    snap = status.snapshot(full=True)  # traces are left out of the default snapshot (size)
    assert snap["behavior_traces"][0]["reason"] == "Test behavior has a reason."
    assert snap["recent_actions"] == ["data_weaver"]
    assert snap["recent_emotions"] == ["Focused"]
    assert snap["recent_sfx"] == ["data_blip"]
    assert snap["recent_animations"] == ["data_weaver"]
    assert "Behavior Chosen" in snap["behavior_audit_log"][0]

    bad = dict(trace)
    bad["reason"] = ""
    try:
        status.record_behavior_trace(bad)
    except ValueError as exc:
        assert "reason is required" in str(exc)
    else:
        raise AssertionError("reasonless trace was accepted")


def test_behavior_trace_keeps_last_100_items():
    from core.status import RuntimeStatus

    status = RuntimeStatus()
    for i in range(105):
        status.record_behavior_trace({
            "timestamp": f"2026-06-03T00:00:{i:02d}+00:00",
            "goal": "test",
            "intent": "test",
            "emotion": "Normal",
            "action": f"action_{i}",
            "animation": "",
            "sfx": "",
            "tool_used": "",
            "reason": f"reason {i}",
            "priority": "low",
            "decision": {},
            "competing_behaviors": [],
            "conflicts": [],
        })

    snap = status.snapshot(full=True)
    assert len(snap["behavior_traces"]) == 100
    assert snap["behavior_traces"][0]["action"] == "action_104"
    assert snap["behavior_traces"][-1]["action"] == "action_5"


def test_emotion_authority_rejects_incompatible_llm_override():
    from brain.emotion_authority import validate_emotion_decision

    decision = validate_emotion_decision("Focused", "Sleepy")

    assert decision.cognitive_emotion == "Focused"
    assert decision.llm_suggested_emotion == "Sleepy"
    assert decision.final_emotion == "Focused"
    assert decision.authority_source == "cognitive_engine"
    assert decision.accepted is False
    assert decision.rejection_reason == "Cognitive emotion is authoritative."


def test_emotion_authority_accepts_confirming_or_compatible_suggestions():
    from brain.emotion_authority import validate_emotion_decision

    confirmed = validate_emotion_decision("Focused", "Focused")
    assert confirmed.final_emotion == "Focused"
    assert confirmed.accepted is True

    refined = validate_emotion_decision("Focused", "Curious")
    assert refined.final_emotion == "Focused"
    assert refined.accepted is True
    assert refined.compatible is True


def test_runtime_status_records_emotion_validation_trace():
    from core.status import RuntimeStatus

    status = RuntimeStatus()
    decision = {
        "cognitive_emotion": "Focused",
        "llm_suggested_emotion": "Sleepy",
        "final_emotion": "Focused",
        "authority_source": "cognitive_engine",
        "accepted": False,
        "rejection_reason": "Cognitive emotion is authoritative.",
        "reason": "LLM emotion conflicts with authoritative cognitive state.",
    }
    status.record_emotion_decision(
        decision,
        goal="answer_user",
        priority="normal",
        behavior_decision={"emotion": "Focused"},
    )

    snap = status.snapshot(full=True)
    trace = snap["behavior_traces"][0]
    assert snap["emotion_decision"] == decision
    assert snap["emotion_disagreements"][0] == decision
    assert trace["event"] == "emotion_validation"
    assert trace["cognitive_emotion"] == "Focused"
    assert trace["llm_suggested_emotion"] == "Sleepy"
    assert trace["final_emotion"] == "Focused"
    assert trace["accepted"] is False


def test_llm_final_emotion_override_path_removed():
    from pathlib import Path

    source = Path("api/websocket_server.py").read_text(encoding="utf-8")
    assert "Final generated reply used emotion tag" not in source
    assert "llm_final_emotion" not in source
    assert "apply_final_reply_emotion" not in source


def test_cognitive_conflict_detection_suppresses_wifi_celebration_overlap():
    from brain.cognitive_state import CognitiveState, decide_behavior

    cog = CognitiveState(
        emotion="Happy",
        energy=80,
        wifiConnected=True,
        charging=False,
        time="12:00",
        currentAction="wifi_search",
        availableTools=[],
        availableAnimations=[],
        availableSFX=[],
        needs={},
        autoVisuals=True,
    )
    decision = decide_behavior(cog, event="success", text="done")

    assert decision.animation == ""
    assert decision.sfx == "success_chime"
    assert decision.conflicts
    assert any("opposite system states" in item for item in decision.conflicts)
    assert "Suppressed conflicting animation" in decision.reason


def test_companion_memory_reminders_and_project_threads():
    from memory.companion_memory import (
        build_daily_stats,
        ensure_companion_memory,
        maybe_capture_reminder,
        maybe_update_project_thread,
    )

    memory = ensure_companion_memory({})
    assert not maybe_capture_reminder("when should we do this?", memory)
    assert maybe_capture_reminder("remind me to flash the firmware tomorrow", memory)
    assert len(memory["reminders"]) == 1
    assert maybe_update_project_thread("we need to fix the esp32 voice bug", "we traced it", memory)
    stats = build_daily_stats(memory)
    assert stats["open_reminders"] == 1
    assert stats["active_project_thread"] == "firmware and hardware behavior"


def test_manual_dashboard_exposes_companion_controls():
    from api.dashboard import DASHBOARD_HTML
    from api.device_control import VALID_ACTIONS, VALID_SFX, VALID_STATES

    assert "/control/action" in DASHBOARD_HTML
    assert "/control/sfx" in DASHBOARD_HTML
    assert "/control/say" in DASHBOARD_HTML
    assert "/control/pc-voice/chunk" not in DASHBOARD_HTML
    assert "joystick" in VALID_ACTIONS
    assert "stickman_duet" not in VALID_ACTIONS
    assert "data_weaver" in VALID_ACTIONS
    assert "glitch_screech" in VALID_SFX
    assert "Normal" in VALID_STATES


def test_control_handlers_reject_bad_payloads_without_500():
    import pytest
    from aiohttp import web
    from api.device_control import energy_handler, serial_connect_handler

    class FakeJsonRequest:
        remote = "127.0.0.1"
        can_read_body = True

        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    with pytest.raises(web.HTTPBadRequest):
        asyncio.run(energy_handler(FakeJsonRequest({"level": "loud"})))

    with pytest.raises(web.HTTPBadRequest):
        asyncio.run(serial_connect_handler(FakeJsonRequest({"port": "", "baud": "fast"})))


def test_pc_voice_chunk_handler_feeds_active_session(monkeypatch):
    from api import device_control

    class FakeRequest:
        remote = "127.0.0.1"
        content_length = 4

        async def read(self):
            return b"\x01\x00\x02\x00"

    class FakeWs:
        closed = False

    class FakeSession:
        closing = False
        ws = FakeWs()

        def __init__(self):
            self.chunks = []

        async def handle_audio(self, chunk):
            self.chunks.append(chunk)

    session = FakeSession()
    monkeypatch.setattr(device_control, "_session", session)

    response = asyncio.run(device_control.pc_voice_chunk_handler(FakeRequest()))
    assert response.status == 200
    assert session.chunks == [b"\x01\x00\x02\x00"]
