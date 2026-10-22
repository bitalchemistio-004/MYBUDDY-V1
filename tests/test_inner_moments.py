"""The inner life thinks at moments that matter, only while the user is there (S53)."""
from datetime import datetime, timedelta

from core import inner_moments as im

NOON = datetime(2026, 9, 27, 13, 0)  # outside the morning/evening anchors


def test_a_conversation_ending_is_a_moment_once():
    m = im.Moments()
    assert m.due(NOON, idle_s=5, upcoming=[]) == "conversation_ended"
    assert m.due(NOON, idle_s=5, upcoming=[]) is None


def test_coming_back_after_being_away():
    m = im.Moments(conversation_ended=False)
    assert m.due(NOON, idle_s=im.AWAY_S + 1, upcoming=[]) is None  # away: nothing, not even a pending moment
    assert m.due(NOON, idle_s=3, upcoming=[]) == "returned"
    assert m.due(NOON, idle_s=3, upcoming=[]) is None


def test_morning_and_evening_anchors_fire_once_a_day():
    m = im.Moments(conversation_ended=False)
    morning = datetime(2026, 9, 27, 8, 30)
    assert m.due(morning, idle_s=1, upcoming=[]) == "morning"
    assert m.due(morning + timedelta(hours=1), idle_s=1, upcoming=[]) is None
    assert m.due(datetime(2026, 9, 27, 19, 0), idle_s=1, upcoming=[]) == "evening"
    assert m.due(datetime(2026, 9, 28, 8, 0), idle_s=1, upcoming=[]) == "morning"


def test_a_reminder_coming_up():
    m = im.Moments(conversation_ended=False)
    soon = [{"id": 7, "due": NOON + timedelta(minutes=10)}, {"id": 8, "due": NOON + timedelta(hours=3)}]
    assert m.due(NOON, idle_s=1, upcoming=soon) == "reminder_soon"
    assert m.due(NOON, idle_s=1, upcoming=soon) is None  # each reminder once


def test_no_anchor_while_not_at_the_pc():
    m = im.Moments(conversation_ended=False)
    assert m.due(datetime(2026, 9, 27, 8, 30), idle_s=im.PRESENT_S + 30, upcoming=[]) is None


def test_showing_is_spaced_out_but_immediate_on_return():
    m = im.Moments()
    assert m.may_show(NOON, None, idle_s=1)
    m.last_shown = NOON
    assert not m.may_show(NOON + timedelta(minutes=5), None, idle_s=1)
    assert m.may_show(NOON + timedelta(minutes=5), "returned", idle_s=1)
    assert not m.may_show(NOON + timedelta(hours=1), None, idle_s=im.PRESENT_S + 1)  # nobody to show it to
    assert m.may_show(NOON + timedelta(seconds=im.SHOW_GAP_S), None, idle_s=1)


def test_blockers(monkeypatch):
    monkeypatch.setattr(im, "on_battery", lambda: False)
    monkeypatch.setattr(im, "fullscreen_busy", lambda: False)
    monkeypatch.setattr(im, "gpu_busy", lambda: False)
    assert im.blockers(False, idle_s=3) == []
    assert im.blockers(True, idle_s=3) == ["do not disturb"]
    assert im.blockers(False, idle_s=im.AWAY_S) == ["user away"]
    monkeypatch.setattr(im, "on_battery", lambda: True)
    monkeypatch.setattr(im, "gpu_busy", lambda: True)
    assert im.blockers(False, idle_s=3) == ["on battery"]  # the GPU check only runs when nothing else blocks
    monkeypatch.setattr(im, "on_battery", lambda: False)
    assert im.blockers(False, idle_s=3) == ["GPU busy"]


def test_parse_batch():
    text = "1. The rain sounds like typing.\n- QUIET\n\n2) I wonder what Lina is doing.\n* ok\n\"The rain sounds like typing.\""
    assert im.parse_batch(text) == ["The rain sounds like typing.", "I wonder what Lina is doing."]
    assert len(im.parse_batch("\n".join(f"Thought number {i} here." for i in range(20)))) == im.BATCH_SIZE


def test_windows_queries_answer_without_raising():
    assert im.idle_seconds() is None or im.idle_seconds() >= 0
    assert isinstance(im.on_battery(), bool) and isinstance(im.fullscreen_busy(), bool)


def test_the_loop_makes_one_call_for_a_batch_and_shows_without_speaking(monkeypatch, tmp_path):
    """End to end with a fake model: the conversation-ended moment makes ONE
    call for several thoughts, shows one as a bubble, speaks nothing."""
    import asyncio
    from types import SimpleNamespace

    import brain.llm_router as router
    import core.state_manager as sm
    from core import companion_settings, ui_settings
    from core.status import runtime_status

    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    ui_settings.reset_cache()
    cs = SimpleNamespace(inner_enabled=True, next_inner_wait=lambda energy: 0)
    monkeypatch.setattr(companion_settings, "get_companion_settings", lambda: cs)
    monkeypatch.setattr(im, "idle_seconds", lambda: 5.0)
    monkeypatch.setattr(im, "blockers", lambda dnd, idle: [])
    monkeypatch.setattr(im, "upcoming_reminders", lambda: [])
    monkeypatch.setattr(sm.random, "randint", lambda a, b: 0)  # the first wait
    monkeypatch.setattr(sm, "choose_inner_life_cue", lambda state, memory: SimpleNamespace(action=None, sfx=None, quiet=False, action_ms=0))
    calls = []

    async def fake_llm(**kw):
        calls.append(kw)
        text = "\n".join(f"{i}. A quiet thought number {i} about the rain." for i in range(1, 7))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    monkeypatch.setattr(router, "llm_chat_create", fake_llm)

    class WS:
        closed = False
        sent = []

        async def send_str(self, s):
            self.sent.append(s)

    ws, spoken, memory = WS(), [], {}
    state = sm.MBUDDYState()
    state.energy = 80

    async def on_thought(text, current):
        spoken.append(text)

    async def go():
        task = asyncio.create_task(sm.inner_life_loop(ws, memory, [False], state, on_thought=on_thought))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if runtime_status.inner_thought.get("text"):
                break
        await asyncio.sleep(0.2)  # a few more cycles: no new call, no second thought (spacing)
        ws.closed = True
        task.cancel()

    runtime_status.update(inner_thought={})
    asyncio.run(go())
    ui_settings.reset_cache()
    assert len(calls) == 1
    assert runtime_status.inner_thought["text"] == "A quiet thought number 1 about the rain."
    assert len(memory["inner_queue"]) == 5
    assert spoken == []
    assert sum("[INNER]" in s for s in ws.sent) == 1
