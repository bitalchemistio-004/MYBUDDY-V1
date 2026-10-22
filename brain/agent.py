"""
brain/agent.py - the hybrid router's "understand and act" step.

Keyword rules (brain/cognitive_state.py) pick a tool instantly for explicit
commands ("set a timer for 5 minutes"). Everything else used to get no tool
at all, so "my head hurts, the volume is high" was answered with "volume
can't be adjusted". This step asks the local model, with native tool calling,
whether what the user said implies an action; the chosen tools run for real, and
their results feed the normal reply as TOOL OUTPUT, so the reply can only
report what actually happened.

Measured before building it (tools/bench_intent.py): 26/30 indirect requests
right on qwen3:4b-instruct, 0 over-eager. See docs/audit/AUDIT_LOG.md 2.8.

Small, reversible actions run immediately. Bigger actions (opening or
changing other apps) will ask first once they exist (docs/PHASE3_BACKLOG.md).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime

import requests

import utils.logger as log
from brain.tools import ToolFailure

_S = {"type": "string"}


def _tool(name: str, desc: str, /, **params) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": params, "required": list(params)},
    }}


TOOLS = [
    _tool("adjust_volume", "Change the PC volume. action: up, down, mute (other apps), unmute, or set.",
          action={"type": "string", "enum": ["up", "down", "mute", "unmute", "set"]},
          amount={"type": "number", "description": "percentage points for up/down, or the level for set"}),
    _tool("set_timer", "Start a countdown timer that only counts down and tells the user when it is over. It cannot do anything "
          "afterwards: to stop the music for a while and have it play again, use media_control with minutes instead.",
          minutes={"type": "number"}),
    _tool("add_reminder", "Put something on the user's schedule: a reminder or a calendar event, at a "
          "time, a date, or after a delay. They are reminded when it's due.",
          text={"type": "string", "description": "what to be reminded of, in the user's words, keeping any label or "
                                                 "code they wrote (\"[S2] send the deck\")"},
          when={"type": "string", "description": "e.g. 'in 20 minutes', 'at 17:00', 'tomorrow 9am'"},
          repeat={"type": "string", "enum": ["once", "hourly", "daily", "weekly"],
                  "description": "once, unless the user says it repeats (every hour, every day, every week)"}),
    _tool("add_task", "Add an item to the task or shopping list (no time).",
          title={"type": "string", "description": "the item in the user's words, keeping any label or code they wrote"}),
    _tool("complete_task", "Mark a task, or a one-off reminder, as done (\"mark X as done\", \"I did X\", \"tick off X\"). "
          "Never add a new task to mark one done.", about={"type": "string", "description": "words from the task's title"}),
    _tool("change_reminder", "Change a reminder or timed task that already exists: move it to another time, or make it repeat or "
          "happen only once (\"move X to 7pm\", \"only once\", \"not every day\"). Never add a second one.",
          about={"type": "string", "description": "words from the reminder's title"},
          when={"type": "string", "description": "the new time, or '' to keep it"},
          repeat={"type": "string", "enum": ["keep", "once", "hourly", "daily", "weekly"]}),
    _tool("list_tasks", "Read out the pending tasks."),
    _tool("get_time", "Get the current local time and date."),
    _tool("get_weather", "The weather now, or the forecast for a day (up to 3 days ahead).",
          location={"type": "string", "description": "a place the user named, or '' for the user's home"},
          day={"type": "string", "description": "now (the default), today, tomorrow, day after tomorrow, a weekday, or a date"}),
    _tool("web_search", "Search the internet for facts that change or live events (news, results, prices), to "
          "answer from. Not for general knowledge you already have.", query=_S),
    _tool("remember", "Save a lasting fact the user tells you about themselves, or a standing preference for how you "
          "should answer or act ('always use bullet points', 'from now on keep it short').", fact=_S,
          kind={"type": "string", "enum": ["fact", "preference"]}),
    _tool("media_control", "Pause, resume, or skip to the next or previous song in the music that is playing. "
          "For a pause that should end by itself (stop the music for a while, then play it again), call it once with "
          "action pause and minutes, which is how long to wait before it plays again; that one call is the whole request. "
          "It only asks the user; nothing happens until they say yes.",
          action={"type": "string", "enum": ["pause", "resume", "next", "previous"]},
          minutes={"type": "number", "description": "pause only: play it again after this many minutes"}),
    _tool("pc_status", "Only when the user asks about their PC: which apps or windows are open, "
          "which one is in front, whether anything is playing sound, battery, CPU or memory."),
    _tool("window_action", "Bring up, minimize or close an open app window. It only asks the user; "
          "nothing happens until they say yes.",
          action={"type": "string", "enum": ["focus", "minimize", "close"]},
          target={"type": "string", "description": "the app or window name"}),
    ]

# ── Areas: first WHAT needs doing, then which tool ─────────────────────────
# The model first picks an area, then sees only that area's tools. From one
# long mixed list, a 4B model grabbed whichever tool had a near name
# ("remember" for "copy this", a timer for "lock my screen", S96). Every tool
# belongs to exactly one area (tests/test_agent_areas.py); a new ability just
# joins one. The descriptions are the catalogue the model chooses from.
AREAS = {
    "sound": ("the PC's volume: louder, quieter, a level, mute or unmute other apps", ("adjust_volume",)),
    "time": ("the current time and date, and countdown timers that ring on THIS PC (not when the user says it is for their phone)", ("get_time", "set_timer")),
    "schedule": ("reminders, calendar events, the task and shopping list, calendar files",
                 ("add_reminder", "add_task", "complete_task", "change_reminder", "list_tasks", "calendar_list", "calendar_export_ics",
                  "calendar_import_ics")),
    "weather": ("the weather anywhere", ("get_weather",)),
    "facts": ("look up something online that changes or that you can't know yourself (news, results, "
              "prices, today's events) for a quick answer now; general knowledge needs no lookup (nothing is kept "
              "or shown to him)", ("web_search",)),
    "memory": ("your memory and notes: save a lasting fact he tells you about his own life, check what your "
               "notes say about something, or learn how to do something you can't yet, or study what he asks you "
               "to learn, and keep it in your notes (you read about it online and come back)",
               ("remember", "search_notes", "learn_about")),
    "apps": ("what's open or playing on the PC and the battery; open, find, bring up, minimize, close or "
             "end apps and windows", ("pc_status", "window_action", "open_app", "find_apps", "kill_app")),
    "music": ("play a song, an artist or a kind of music; pause, resume or skip; what's playing",
              ("music_play", "music_now_playing", "media_control", "choose_app")),
    "browser": ("open a search or a website in his browser, for him to see and browse himself",
                ("browser_search", "open_url")),
    "files": ("create, read, list, open or delete files and folders; take a screenshot of the screen (saved in Documents\\MBUDDY\\Screenshots)",
              ("create_file", "read_file", "list_folder", "open_file_or_folder", "delete_file", "take_screenshot")),
    "office": ("write text into Word (it opens Word itself) and save it", ("word_write",)),
    # {apps} is filled with the user's connected apps (area_tools_step1).
    "create": ("do or make something inside one of the apps the user connected ({apps}); it opens the app "
               "itself and learns the app first if it's new", ("use_app",)),
    "settings": ("any Windows settings page (display, sound, network, Bluetooth, notifications and more), "
                 "screen brightness, lock the PC, shut down or restart",
                 ("open_settings", "set_brightness", "lock_screen", "power")),
    "clipboard": ("read or set what the user copied", ("clipboard_read", "clipboard_write")),
    "routines": ("run the user's saved routines (a sequence of actions they made)", ("run_routine",)),
    "cloud": ("a hard question for a much bigger cloud model: exact reasoning, comparisons, long writing, or "
              "reading the clipboard, a file or the screen (that one asks first)", ("ask_bigger_model", "ask_bigger_model_about")),
    "messages": ("which messages are waiting for an answer (WhatsApp and the other chat apps the user chose to watch) and "
                 "what a window on the screen shows, when the user asks what they missed or what is on screen",
                 ("missed_messages", "read_window")),
    "phone": ("anything the user wants on their PHONE (\"on my phone\", \"to my phone\", \"remind my phone\"): an alarm, a timer, a "
              "calendar event, a call, a text or WhatsApp message, directions, a search (they tap it on the phone)", ("phone_do",)),
    "day": ("the user's day: plan today around the calendar and open tasks, how long they were in each app, find something "
            "across their tasks, calendar and your notes", ("plan_day", "time_report", "find_anything", "stop_repeating_reminder")),
    "habits": ("their habits and streaks (they tell you what they did), and the apps that distract them during a focus sprint",
               ("habit_log", "habit_status", "distraction_list")),
    "abilities": ("what you are able to do, when the user asks", ("list_abilities",)),
}


def _spec(name: str) -> dict | None:
    for t in TOOLS:
        if t["function"]["name"] == name:
            return t
    from brain import pc_actions
    ability = pc_actions.REGISTRY.get(name)
    return ability.spec() if ability else None


def area_tools(area: str) -> list[dict]:
    """The tools of one area that exist and are switched on."""
    names = AREAS.get(str(area), ("", ()))[1]
    return [t for t in (_spec(n) for n in names) if t and not tool_switched_off(t["function"]["name"])]


_PHONE_TARGET = re.compile(r"\b(?:on|to|for|in|from)\s+(?:my|the)\s+phone\b|\bremind\s+my\s+phone\b|\bmy\s+phone\s+(?:alarm|timer)\b", re.I)
_PHONE_DOES = re.compile(r"\b(?:timer|alarm|remind\w*|calendar|event|call|text|message|whatsapp|directions?|navigate|search|set|put|add|send)\b", re.I)


def area_tools_step1() -> list[dict]:
    """Step 1's tools: one per area that has a switched-on tool, no
    parameters. Calling one means "this area is needed"; its real tools
    come in step 2."""
    # A required "request": with no inputs, the model answered in text ("I
    # can't open Wi-Fi settings from here") instead of routing (S96).
    tools = []
    for area, (desc, _names) in AREAS.items():
        text = describe_area(desc)
        if text and area_tools(area):
            tools.append(_tool(area, text[0].upper() + text[1:] + ".",
                               request={"type": "string", "description": "what the user wants done here, in a few words"}))
    return tools


def describe_area(desc: str) -> str:
    """An area's catalogue line, with {apps} filled from the apps the user
    connected; "" when it needs connected apps and there are none."""
    if "{apps}" not in desc:
        return desc
    from core import ui_settings
    names = [a["name"] for a in ui_settings.connected_apps()]
    return desc.replace("{apps}", ", ".join(names)) if names else ""


_PLANNER = (
    "You are MBUDDY, a warm desktop companion who can act on this PC. Each tool here is an area of what "
    "you can do; calling one gives you its real tools next. Listen for what USER_NAME needs, not only for "
    "commands. Call the area that can take care of it when he asks for something, when what he says "
    "implies a need an area can handle, when he tells you a lasting fact about his life, or when answering "
    "needs something you can't know yourself: the time, his schedule, the weather, something that changes "
    "(news, results, prices, today's events), or what's happening on his PC. Call two areas only for two "
    "different things. Choose by what needs doing, "
    "not by the words he happens to use. "
    "Implied needs: running out of something, unfinished work, or something he must do by a time -> "
    "schedule. Too loud, or a call is over while other apps are muted -> sound. "
    "Act only on his message now; messages marked (earlier) were said before and only help you understand "
    "it (a yes answering your question, 'it', 'that one'): never act on them again. "
    "Feelings, chat, opinions, ideas, suggestions, advice, explanations, plans, lists, step-by-step "
    "instructions, jokes and general knowledge that doesn't change need no area: call nothing, and you "
    "will give them to him yourself in your reply. "
    "Questions about you (whether you can speak or hear, what you have, how you are, what you are doing, "
    "whether you are working or done) need no area either: you know yourself, and you will answer. "
    "If he asks you to DO something on the PC or in an app that no other area can do, or asks you to "
    "learn or study how to do something, call memory to learn it. "
    "You don't answer the user in this step: you only pass the request to the right area, so never reply "
    "with text when an area can do it, and never refuse an action: if no other area can do it, the "
    "memory area learns it."
)

_SYSTEM = (
    "You are MBUDDY, a warm desktop companion who can act on this PC through tools. "
    "Listen for what USER_NAME needs, not just the literal words. When what he says implies something a tool "
    "can fix, call the tool. For small, reversible actions (volume, timers, reminders, tasks, weather, "
    "saving a fact he tells you) act without asking. "
    "Hints: running out of something -> add_task. Something he must do by a time or day -> add_reminder. "
    "Unfinished work he mentions -> add_task. If apps are muted and the reason has ended -> unmute. "
    "News, sports results, prices, recent events, or any fact "
    "you are not sure of -> web_search. Facts about his family, health, likes or life -> remember. "
    "You are given only the tools of the area that fits what he said: use the one that does it. "
    "Act only on his message now; messages marked (earlier) only help you understand it. "
    "Only act when a need is actually implied: feelings, chat and opinions need no tool. "
    "When learn_about is one of your tools, use it for anything he wants you to do or know that you "
    "can't yet. Otherwise, if none of your tools does what he asks, call no tool; never use a different "
    "tool as a stand-in. "
    "Call each tool at most once. If no tool fits, call none."
)


def other_apps_muted() -> bool:
    """Whether any app other than MBUDDY is muted (so "the call is over" can unmute)."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    from brain.tools import is_mbuddy_voice

    comtypes.CoInitialize()
    try:
        return any(
            s.Process is not None and not is_mbuddy_voice(s.Process) and s.SimpleAudioVolume.GetMute()
            for s in AudioUtilities.GetAllSessions()
        )
    finally:
        from brain.com_guard import end as _com_end; _com_end()


def _rules_line(user_text: str) -> str:
    """What it learned that applies to THIS request: the decision is where it matters (W5)."""
    try:
        from brain import brain_store
        rules = brain_store.recall_rules(user_text)
        return f" What you learned that applies (follow it): {rules}" if rules else ""
    except Exception:
        return ""


def _state_line() -> str:
    """What MBUDDY can see right now, so the model picks sensible values."""
    # The full date too: without it, searches were dated with the model's
    # training years ("recent horror movie 2024-2025", live 2026-09-27).
    parts = [f"Now: {datetime.now().strftime('%A %d %B %Y, %H:%M')}."]
    try:  # what it is, so a question about itself isn't sent off to be learned (F1)
        from core import ui_settings
        voice = ("you speak out loud through the PC's speakers, so he and people near him hear you"
                 if ui_settings.get("speak_replies") else "your voice is off (replies are text)")
        ears = "you hear through the microphone" if ui_settings.get("microphone_on") else "the microphone is off"
        parts.append(f"You: {voice}; {ears}.")
    except Exception:
        pass
    try:  # the moment (what is in front, today's time, the next event): a question it answers needs no tool to be asked
        from brain import awareness
        moment = awareness.block()
        if moment:
            parts.append(moment.replace("RIGHT NOW, read moments ago (use only what the user's words are about; never recite this list):",
                                        "What you know right now (answer from it when his words are about it; no tool is needed for these):"))
    except Exception:
        pass
    try:  # work already running in the background: don't start it a second time (W1)
        from core import jobs
        for j in jobs.running()[:2]:
            what = f"working in {j['subject']} on \"{j['task'][:60]}\"" if j["kind"] == "app" else f"learning about {j['subject']}"
            parts.append(f"Already running in the background: {what}.")
    except Exception:
        pass
    try:
        from brain import action_log
        done = action_log.recent(3)
        if done:
            parts.append("What you really did last: " + "; ".join(a["result"] for a in done) + ".")
    except Exception:
        pass
    try:
        from brain.tools import execute_volume_tool
        level = execute_volume_tool("what's the volume")
        if not isinstance(level, ToolFailure):
            parts.append(level)
    except Exception:
        pass
    try:
        if other_apps_muted():
            parts.append("Other apps are muted right now (MBUDDY muted them).")
    except Exception:
        pass
    try:
        from brain.tools import _timer_manager
        timers = _timer_manager.list_timers()
        if timers:
            parts.append("Running timers: " + ", ".join(t.label for t in timers) + ".")
    except Exception:
        pass
    return " ".join(parts)


def last_question(reply: str) -> str:
    """Only the question at the end of a reply ("Better?"), without the claim
    before it ("I lowered the volume to 30%." never happened, live)."""
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\[[A-Z]+\]", "", reply or "").strip())
    question = sentences[-1] if sentences and sentences[-1].endswith("?") else ""
    # Only an offer, which a "yes" answers ("Want me to...?"). "Better?" after
    # a false "I lowered it" implied it was done, and "still loud" got no tool.
    if question and (len(sentences) == 1 or re.match(
            r"(want me to|should i|shall i|do you want|would you like|can i|may i|how about)\b", question, re.I)):
        return question
    return ""


def build_messages(user_text: str, history: list, step: int = 2, routed: str = "") -> list[dict]:
    """
    The system prompt and tools never change, so Ollama keeps them cached
    and only reads what is new each turn. The live state (time, volume)
    goes at the end, with the user's words: inside the system prompt it made
    the whole ~950-token prompt differ every turn, and on this GPU (~100
    prompt tokens/s) that cost ~8 s per turn.
    """
    from core.ui_settings import personalize  # the user's name from the app
    messages = [{"role": "system", "content": personalize(_PLANNER if step == 1 else _SYSTEM)}]
    # What the user said recently, but not MBUDDY's own earlier replies: they
    # can be refusals or claims that never happened, and the model copied
    # them (live: "I won't add fake ones" -> three direct orders got no tool;
    # "I lowered it to 30%" -> "still loud" got nothing). What really happened
    # is in the state line (real actions). A question MBUDDY just asked is kept,
    # so "yes" still has something to answer.
    recent = [m for m in (history or [])[-6:] if isinstance(m, dict) and m.get("content")]
    # Earlier messages are one context note, not a row of user turns: a row
    # looked like pending requests and one was done again ("You're the best."
    # after "Play some jazz." played jazz; S99, again at temperature 0).
    earlier = [str(m["content"])[:300] for m in recent if m.get("role") == "user"]
    if earlier:
        # "Context only", not "already handled": a request that failed and is
        # asked again is a new request (live: the repeat did nothing, S109).
        messages.append({"role": "user", "content": "Earlier he said (context only; his message now is what to act on): "
                         + " | ".join(earlier)})
    if recent and recent[-1].get("role") == "assistant" and last_question(str(recent[-1]["content"])):
        messages.append({"role": "assistant", "content": last_question(str(recent[-1]["content"]))})
    # Step 2 acts on what step 1 worked out (live bench: with earlier messages
    # about the same event, "add it to my calendar" checked the calendar instead).
    task = f"\n[What needs doing here: {routed}]" if routed and step == 2 else ""
    messages.append({"role": "user", "content": f"[Current state: {_state_line()}{_rules_line(user_text)}]\n{user_text}{task}"})
    return messages


# Which Extensions setting gates each tool (docs/PHASE3_BUILD_PLAN.md).
TOOL_SETTING = {
    "adjust_volume": "volume", "set_timer": "timers", "add_reminder": "timers",
    "add_task": "timers", "list_tasks": "timers", "complete_task": "timers", "change_reminder": "timers", "web_search": "search", "remember": "memory",
    "pc_status": "windows", "window_action": "windows", "media_control": "windows",
}


_ABILITY = {
    "adjust_volume": "change the PC volume (up, down, set, mute other apps)",
    "set_timer": "start a timer",
    "add_reminder": "put reminders and calendar events on the user's schedule",
    "add_task": "add to the task or shopping list",
    "complete_task": "mark a task or reminder as done",
    "change_reminder": "move a reminder to another time, or make it repeat or happen once",
    "list_tasks": "read the task list",
    "get_time": "tell the time and date",
    "get_weather": "get the weather",
    "web_search": "search the internet",
    "remember": "remember facts the user tells you",
    "pc_status": "see which apps are open, what's playing, the volume and battery",
    "window_action": "bring up, minimize or close an open window (asks the user first)",
    "media_control": "pause, resume or skip the music that's playing (asks the user first)",
}


def capability_text() -> str:
    """What MBUDDY can really do, from the tools that are switched on, and the
    rule for everything else (live: it promised to switch the song to
    "Riptide" with no way to do it, and offered to "skip or loop" it)."""
    # From the areas the switched-on tools cover: the same catalogue the
    # model picks from, so what it says it can do matches what it can reach.
    can = [describe_area(desc) for area, (desc, _names) in AREAS.items()
           if area != "abilities" and area_tools(area) and describe_area(desc)]
    # What exists but the user switched off: said as that, not as "I can't" (the abilities live in Extensions).
    off = [area for area, (desc, names) in AREAS.items()
           if area != "abilities" and describe_area(desc) and any(_spec(n) for n in names) and not area_tools(area)]
    switched_off = (" SWITCHED OFF by him in Extensions: " + ", ".join(off) + ". If he asks for one of these, say it is switched "
                    "off and that he can turn it on in Extensions; do not say you cannot do it.") if off else ""
    return ("YOUR ABILITIES RIGHT NOW (only these, through your tools): " + "; ".join(can) + ". " + switched_off + " "
            "That is all you can do, and your tools run before you speak. When he asks for anything "
            "else, or no TOOL OUTPUT shows it done, tell him warmly that you can't do that yet and offer "
            "the closest thing you can. Never say you did it, are doing it, or will do it, unless TOOL "
            "OUTPUT or YOUR BACKGROUND WORK shows it.")


def tool_switched_off(name: str) -> bool:
    """True when the user turned this tool off in the app (Extensions, or internet search)."""
    from core import ui_settings
    from brain import pc_actions
    ability = pc_actions.REGISTRY.get(name)
    if ability is not None and not ui_settings.extension_enabled(ability.topic):
        return True  # its group is switched off in Extensions (S93)
    if name == "web_search" and not ui_settings.get("internet_search"):
        return True
    setting = TOOL_SETTING.get(name)
    return bool(setting) and not ui_settings.tool_allowed(setting)


def allowed_tools() -> list[dict]:
    """The tools offered to the model: switched-off ones are left out."""
    return [t for t in TOOLS if not tool_switched_off(t["function"]["name"])]


def _chat_model() -> str:
    from core import ui_settings
    return ui_settings.get("llm_model") or os.getenv("OLLAMA_CHAT_MODEL", "qwen3:4b-instruct")


def _ollama_chat_url(priority: str = "turn") -> str:
    """The chat address. [priority] says whom the call is for ("turn" the user's turn, "job" work they asked for that runs
    on its own, "background" housekeeping): the cloud answers one call at a time and the user's turn goes first."""
    from core import model_gate
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    return model_gate.url_with((base[:-3] if base.endswith("/v1") else base) + "/api/chat", priority)


async def _ask_model(messages: list[dict], tools: list[dict] | None = None, num_predict: int = 120) -> list[dict]:
    """The model's tool calls for this turn: [{"name", "args"}]; [] for none.
    Raises when Ollama answers with an error (logged; never a silent "no
    action"). A tool call cut off by the output budget is retried once with
    room (live: "invalid tool call arguments ... unexpected end of JSON", S98)."""
    body = {
        "model": _chat_model(),
        "stream": False, "think": False, "tools": allowed_tools() if tools is None else tools, "messages": messages,
        # Greedy: choosing a tool is a classification, and at 0.2 the same
        # sentence routed differently from run to run (S104).
        "options": {"temperature": 0, "num_predict": num_predict},
        # Keep the model on the GPU between turns. After Ollama's default 5
        # idle minutes it unloads, and the reload outlasted this timeout.
        "keep_alive": "30m",
    }

    def post():
        return requests.post(_ollama_chat_url(), json=body, timeout=30).json()

    data = await asyncio.to_thread(post)
    if data.get("error"):
        log.warn(f"[Agent] Ollama error: {str(data['error'])[:160]}")
        if "tool call arguments" in str(data["error"]) and num_predict < 700:
            return await _ask_model(messages, tools, 700)
        raise RuntimeError(f"Ollama: {str(data['error'])[:120]}")
    ns = 1e9
    _ask_model.last_text = str((data.get("message") or {}).get("content") or "")
    _ask_model.last = (f"model {data.get('total_duration', 0) / ns:.2f}s: prompt {data.get('prompt_eval_count', 0)} tok "
                       f"{data.get('prompt_eval_duration', 0) / ns:.2f}s, output {data.get('eval_count', 0)} tok "
                       f"{data.get('eval_duration', 0) / ns:.2f}s")
    return [
        {"name": c["function"]["name"], "args": c["function"].get("arguments") or {}}
        for c in (data.get("message", {}).get("tool_calls") or [])
    ]


# ── Executors: each runs a real tool and returns its result text ─────────

def _volume(args, memory):
    from brain.tools import apply_volume_action
    return apply_volume_action(str(args.get("action", "")), args.get("amount"))


def _timer(args, memory):
    from brain.tools import execute_timer_tool
    minutes = float(args.get("minutes") or 0)
    if minutes <= 0:
        return ToolFailure("No timer length was given.")
    return execute_timer_tool(f"set a timer for {max(1, round(minutes * 60))} seconds")


def _reminder(args, memory):
    from brain.task_tracker import parse_time_reference, task_tracker
    text = str(args.get("text", "")).strip()
    when = str(args.get("when", "")).strip()
    repeat = str(args.get("repeat", "") or "").lower()
    if repeat == "once":        # the default: one reminder, at one time (the field test: every reminder became daily)
        repeat = ""
    if repeat:
        from brain.task_tracker import RECURRENCE
        if repeat not in RECURRENCE:
            return ToolFailure(f"I can repeat a reminder every hour, day or week, not '{repeat}'.")
        if not text:
            return ToolFailure("What should the repeating reminder say?")
        start = parse_time_reference(when) if when else None
        first = datetime.fromisoformat(start) if start else datetime.now() + RECURRENCE[repeat]
        task_tracker.create_recurring_reminder(text, first, repeat)
        every = {"hourly": "every hour", "daily": "every day", "weekly": "every week"}[repeat]
        return f"Reminder set: {text}, {every}. The first one is {first.strftime('%a %d %b at %H:%M')}."
    due = parse_time_reference(when) if when else None
    if not text or not due:
        return ToolFailure(f"I couldn't understand the time '{when}', so no reminder was set.")
    task = task_tracker.create_task(text, due_date=due)
    return f"Reminder set: {task.title} on {datetime.fromisoformat(due).strftime('%a %d %b %Y at %H:%M')}."


def _task(args, memory):
    from brain.task_tracker import task_tracker
    title = str(args.get("title", "")).strip()
    if not title:
        return ToolFailure("No task was given.")
    task = task_tracker.create_task(title)
    return f"Added task #{task.task_id}: {task.title}"


def _match_open(about: str):
    """The open tasks and reminders that [about] names, best match first; (items, words)."""
    from brain.connectors.day import _words
    from brain.task_tracker import task_tracker
    stop = {"the", "and", "for", "task", "reminder", "remind", "done", "mark", "move", "change", "make", "only", "once", "my", "every", "day"}
    words = [w for w in _words(about) if w not in stop]
    items = task_tracker.find_open(words)
    while not items and len(words) > 1:          # drop a word at a time ("my qa supplier reminder" -> "supplier")
        words = words[:-1]
        items = task_tracker.find_open(words)
    return items, words


def _when_text(item: dict) -> str:
    when = item.get("when")
    try:
        t = datetime.fromisoformat(when).strftime("%a %d %b at %H:%M") if when else "no time"
    except ValueError:
        t = str(when)
    every = {"hourly": "every hour", "daily": "every day", "weekly": "every week"}.get(item.get("repeat") or "", "once")
    return f"{t}, {every}" if when else t


def _complete_task(args, memory):
    from brain.task_tracker import task_tracker
    items, _w = _match_open(str(args.get("about", "")))
    tasks = [i for i in items if i["kind"] == "task"]
    if not items:
        return ToolFailure(f"I found no open task or reminder about '{args.get('about', '')}'.")
    if not tasks:
        return ToolFailure(f"'{items[0]['title']}' is a repeating reminder, not a task: say stop it if you want it to stop.")
    if len(tasks) > 1:
        return ToolFailure("More than one open task matches: " + "; ".join(f"#{t['id']} {t['title']}" for t in tasks[:5]) + ". Which one?")
    t = tasks[0]
    if not task_tracker.complete_task(t["id"]):
        return ToolFailure(f"Task #{t['id']} could not be marked done.")
    return f"Marked done: task #{t['id']} {t['title']}."


def _change_reminder(args, memory):
    from brain.task_tracker import parse_time_reference, task_tracker
    items, _w = _match_open(str(args.get("about", "")))
    timed = [i for i in items if i["kind"] == "reminder" or i["when"]]
    if not timed:
        return ToolFailure(f"I found no reminder about '{args.get('about', '')}' to change.")
    if len(timed) > 1 and len({i["title"].lower() for i in timed}) > 1:
        return ToolFailure("More than one reminder matches: " + "; ".join(f"{i['title']} ({_when_text(i)})" for i in timed[:5]) + ". Which one?")
    when_text = str(args.get("when", "") or "").strip()
    when = None
    if when_text:
        parsed = parse_time_reference(when_text)
        if not parsed:
            return ToolFailure(f"I couldn't understand the time '{when_text}', so nothing was changed.")
        when = datetime.fromisoformat(parsed)
    repeat = str(args.get("repeat", "keep") or "keep").lower()
    repeat = "" if repeat == "keep" else repeat
    if not when and not repeat:
        return ToolFailure("What should change: the time, or whether it repeats?")
    main, extra = timed[0], timed[1:]          # same title more than once: duplicates; keep one, stop the rest
    now = task_tracker.reschedule(main, when, repeat)
    for d in extra:
        if d["kind"] == "task":
            task_tracker.complete_task(d["id"])
        else:
            task_tracker.complete_reminder(d["id"])
    now = task_tracker.get_open(now["kind"], now["id"])          # what is really stored now, not what was meant
    if not now:
        return ToolFailure("I changed it, but reading it back failed, so I can't say what it is now.")
    gone = f" and removed {len(extra)} duplicate{'s' if len(extra) != 1 else ''}" if extra else ""
    return f"Changed: {now['title']} is now {_when_text(now)}{gone}."


def _list_tasks(args, memory):
    from brain.task_tracker import format_task_list, task_tracker
    return format_task_list(task_tracker.get_pending_tasks())


def _time(args, memory):
    from brain.tools import execute_clock_tool
    return execute_clock_tool()


def _weather(args, memory):
    from brain.tools import execute_weather_tool
    location = str(args.get("location", "")).strip()
    # Not a place: the user's home (Settings) is meant ("current location" was looked up as a town in the USA)
    home_words = ("", "near me", "here", "current location", "my location", "my current location", "my area", "home", "my home",
                  "where i am", "where i live", "my city", "local", "my place", "around me", "outside")
    return execute_weather_tool("" if location.lower() in home_words else location, str(args.get("day", "") or "now"))


def _search(args, memory):
    from brain.tools import execute_web_search_tool
    query = str(args.get("query", "")).strip()
    return execute_web_search_tool(query) if query else ToolFailure("No search query was given.")


def _remember(args, memory):
    from memory.facts import remember_fact, remember_preference
    fact = str(args.get("fact", "")).strip()
    if memory is None:
        return ToolFailure("Memory isn't available right now.")
    if str(args.get("kind", "")).lower() == "preference":
        outcome = remember_preference(memory, fact)
        if outcome == "rejected":
            return ToolFailure("There was nothing clear enough to save.")
        return f"Saved as a preference I'll follow from now on: {fact}" + (" (already saved)" if outcome == "known" else "")
    before = list(memory.get("core_facts") or [])
    outcome = remember_fact(memory, fact)
    if outcome == "rejected":
        return ToolFailure("There was nothing clear enough to save.")
    gone = [f for f in before if f not in (memory.get("core_facts") or [])]
    return (f"Saved to memory: {fact}" + (" (already known)" if outcome == "reinforced" else "")
            + (f" (it replaces: {'; '.join(gone)})" if gone else ""))


def _pc_status(args: dict, memory) -> str:
    from brain.pc_windows import pc_status_text
    return pc_status_text()


def _media_control(args: dict, memory) -> str:
    # Asks first, like window actions; the song change is checked afterwards.
    from brain.pc_windows import request
    try:
        minutes = float(args.get("minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0.0
    return request(str(args.get("action", "")), "", minutes)


def _window_action(args: dict, memory) -> str:
    # Never acts here: it stores the action and the reply asks (brain/pc_windows.py).
    from brain.pc_windows import request
    return request(str(args.get("action", "")), str(args.get("target", "")))


EXECUTORS = {
    "adjust_volume": _volume, "set_timer": _timer, "add_reminder": _reminder,
    "add_task": _task, "list_tasks": _list_tasks, "get_time": _time,
    "complete_task": _complete_task, "change_reminder": _change_reminder,
    "get_weather": _weather, "web_search": _search, "remember": _remember,
    "pc_status": _pc_status, "window_action": _window_action, "media_control": _media_control,
}


def _pc_action(name: str):
    # Critical abilities only ask here; pc_actions.resolve() runs them on a yes.
    def run(args: dict, memory) -> str:
        from brain import pc_actions
        return pc_actions.run(name, args)
    return run


def _register_pc_actions() -> None:
    from brain import pc_actions
    for name in pc_actions.REGISTRY:
        EXECUTORS.setdefault(name, _pc_action(name))
        TOOL_SETTING.setdefault(name, "windows")


_register_pc_actions()


def _asking_tools() -> set[str]:
    from brain import pc_actions
    return {"window_action", "media_control"} | {n for n, a in pc_actions.REGISTRY.items() if a.risk == "critical"}


def execute_calls(calls: list[dict], memory: dict | None) -> list[tuple[str, dict, str]]:
    """Run each distinct call once; unknown tools and errors become failures."""
    results, seen, asked = [], set(), False
    _ASKING = _asking_tools()
    for call in calls:
        name, args = call.get("name", ""), call.get("args") or {}
        key = (name, json.dumps(args, sort_keys=True))
        if key in seen:
            continue  # the model sometimes repeats a call in one turn
        seen.add(key)
        executor = EXECUTORS.get(name)
        if asked and name in _ASKING:
            # Only one question can wait; a second would replace the first
            # (docs/audit/SENIOR_REVIEW.md, M5).
            results.append((name, args, ToolFailure(f"Not asked yet: {name} waits until the first question is answered.")))
            continue
        if executor is not None and tool_switched_off(name):
            results.append((name, args, ToolFailure(f"{name} is switched off in MBUDDY's settings, so it wasn't used.")))
            continue
        if executor is None:
            results.append((name, args, ToolFailure(f"There is no tool called {name}.")))
            continue
        try:
            result = executor(args, memory)
            results.append((name, args, result))
            asked = asked or str(result).startswith("NOT DONE YET")
            # Reads aren't actions; a window action is logged when it's confirmed and done.
            from brain.pc_actions import NOT_LOGGED
            if not isinstance(result, ToolFailure) and name not in ("get_time", "list_tasks", "pc_status", "window_action", "media_control",
                                                                  "get_weather", "web_search") + NOT_LOGGED:
                from brain import action_log
                action_log.record(name, args, result)
        except Exception as exc:
            log.warn(f"[Agent] {name} failed: {exc}")
            results.append((name, args, ToolFailure(f"{name} failed.")))
    return results


_PATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s\"']+\\)*([^\\\s\"']+\\[^\\\s\"']+)")


def _spoken(result: str) -> str:
    """A tool's result as a line to say: no FAILED/NOT prefixes, no bracketed
    diagnostics, long paths cut to their last two parts."""
    text = re.sub(r"^\s*((FAILED|NOT PLAYING|NOT DONE YET)\s*[:-]\s*)+", "", str(result))
    text = re.sub(r"\s*\([^()]*\)", "", text)
    text = _PATH.sub(lambda m: m.group(1), text)
    return re.sub(r"\s+", " ", text).strip()


def spoken_results(results: list[tuple[str, dict, str]]) -> list[str]:
    """
    What every PC action this turn did, as lines code says itself. The model
    only adds a warm sentence after them, so it can't claim more than
    happened (live: Chrome opened and the reply said YouTube Music "started
    playing"; S96). Reads go to the model as TOOL OUTPUT as before, and a
    question waiting for a yes is asked elsewhere.
    """
    from brain import pc_actions
    lines = []
    for name, _args, result in results or []:
        ability = pc_actions.REGISTRY.get(name)
        if ability is None or ability.risk == "read" or str(result).startswith("NOT DONE YET"):
            continue
        line = _spoken(result)
        if line:
            lines.append(line if line.endswith((".", "!", "?")) else line + ".")
    return lines


def says_whole_reply(results: list[tuple[str, dict, str]]) -> bool:
    """True when every action this turn answers in full by itself (Ability
    says_all), so the model adds nothing after what code says."""
    from brain import pc_actions
    acting = [(pc_actions.REGISTRY.get(n), r) for n, _a, r in results or []
              if pc_actions.REGISTRY.get(n) is not None and pc_actions.REGISTRY[n].risk != "read"]
    return bool(acting) and all(getattr(ab, "says_all", False) and not isinstance(r, ToolFailure) for ab, r in acting)


def format_tool_output(results: list[tuple[str, dict, str]]) -> str:
    """The block the reply prompt receives, same shape as the keyword path's."""
    lines = []
    for name, _args, result in results:
        tag = "TOOL FAILED" if isinstance(result, ToolFailure) else "TOOL OUTPUT"
        lines.append(f"\n\n{tag} ({name}):\n{result}\n")
    return "".join(lines)


_STILL_LOUD = re.compile(r"\b(still|too|so|really|way)\s+(too\s+)?(loud|high)\b", re.IGNORECASE)
_STILL_QUIET = re.compile(r"\b(still|too|so|really|way)\s+(too\s+)?(quiet|low)\b|\bcan'?t hear\b", re.IGNORECASE)


def volume_followup_call(user_text: str) -> dict | None:
    """
    "It's still high" right after a volume change is about the volume. With
    the conversation in its context the model saw its own "I lowered it to
    60%" and did nothing (harness, 2026-09-26); the action log, not the
    model's memory of its reply, decides that this is a follow-up.
    """
    from brain import action_log
    recent = action_log.recent(1)
    if not recent or recent[-1]["tool"] not in ("adjust_volume", "sys_volume"):
        return None
    text = (user_text or "").replace("’", "'")
    if _STILL_LOUD.search(text):
        return {"name": "adjust_volume", "args": {"action": "down"}}
    if _STILL_QUIET.search(text):
        return {"name": "adjust_volume", "args": {"action": "up"}}
    return None


_RAN_OUT = re.compile(
    r"\b(?:we'?re|we are|i'?m|i am|we'?ve run|i'?ve run|we ran|i ran|ran|running|run)\s+out of\s+"
    r"(?:the\s+|my\s+|our\s+)?([a-z][a-z ]{1,30}?)\s*(?:[.!?,]|$|\band\b|\bagain\b)",
    re.IGNORECASE,
)


def ran_out_of(user_text: str) -> str | None:
    """
    "We're out of milk" -> "milk". The model added this task in most runs
    but not all (harness, 2026-09-26: "That's a bummer" and no task), so the
    plain case doesn't depend on it.
    """
    m = _RAN_OUT.search((user_text or "").replace("’", "'"))
    if not m:
        return None
    item = m.group(1).strip()
    return None if item.lower() in ("time", "ideas", "energy", "patience", "breath", "luck") else item


def drop_writes_for_a_question(calls: list[dict]) -> list[dict]:
    """
    A question about the state ("Are you sure it's at 50?") is not a request
    to change it: live, the model answered it by setting the volume to 50.
    Reads stay, and so do up/down/mute, because "Are you sure? It's still
    high." is a complaint that should still lower the volume.
    """
    kept = []
    for call in calls:
        name, args = call.get("name"), call.get("args") or {}
        if name in ("set_timer", "add_reminder", "add_task", "remember") or (
                name == "adjust_volume" and str(args.get("action")) == "set"):
            log.info(f"[Agent] ignored {name}{args} for a question about the state")
            continue
        kept.append(call)
    return kept


# Actions that are critical (closing, deleting, sending...): only ever in a task when the user said them
# ("remove the default cube" is work inside the app, so remove/delete aren't here; the script sandbox can't touch files)
_RISKY = re.compile(r"\b(close|quit|exit|kill|terminate|uninstall|shut\s*down|restart|reboot|log\s*off|send|"
                    r"e-?mail|publish|overwrite)\b", re.I)


def drop_added_actions(calls: list[dict], user_text: str) -> list[dict]:
    """
    The task the model writes for an app is the user's words, not more (F55: "I got like 3 blenders open lol"
    became "...and close the extra Blender windows"). A part of the task with a critical verb the user never
    said is dropped; a task that was only that is dropped whole.
    """
    said = {m.group(1).lower().replace(" ", "") for m in _RISKY.finditer(user_text or "")}
    kept = []
    for call in calls:
        args = call.get("args") or {}
        task = str(args.get("task", ""))
        if call.get("name") != "use_app" or not task:
            kept.append(call)
            continue
        parts = [p for p in re.split(r"\s*(?:[,;]|\band then\b|\bthen\b|\band\b)\s*", task) if p.strip()]
        ok = [p for p in parts if not any(m.group(1).lower().replace(" ", "") not in said for m in _RISKY.finditer(p))]
        if len(ok) == len(parts):
            kept.append(call)
        elif ok:
            log.info(f"[Agent] use_app task {task!r}: dropped an action the user never asked for")
            kept.append({**call, "args": {**args, "task": " and ".join(ok)}})
        else:
            log.info(f"[Agent] ignored use_app {task!r}: an action the user never asked for")
    return kept


# A message that refers back to the conversation, so the earlier messages are
# needed to understand it: an answer ("yes", "sure", "go ahead") or a
# reference ("it", "that", "again", "the same", "one").
# Not a bare "that"/"it": "Haha, that's funny." isn't a reference, and seen
# with the earlier messages it redid the earlier request.
_REFERS_BACK = re.compile(
    r"^\s*(yes|yeah|yep|sure|ok(ay)?|go ahead|do it|please do|no|nope)\b"
    r"|\b(again|same|still|instead|another)\b"
    r"|\b(do|play|open|make|set|try|turn|change|put|write|send|add|use|show|fix|close|stop|undo|redo|save|delete"
    r"|remove|skip|pause|resume)\s+(it|that|this|them|one|those|these)\b",
    re.IGNORECASE,
)


async def _fill_args(tool: dict, user_text: str, routed: str) -> dict | None:
    """The arguments for one tool, as a JSON object from the model; None when
    a required one is missing."""
    fn = tool["function"]
    ask = (f"Tool: {fn['name']}: {fn['description']}\nParameters: {json.dumps(fn['parameters'].get('properties', {}))}\n"
           f"What the user said: {user_text}\n" + (f"What needs doing: {routed}\n" if routed else "")
           + "Reply with only one JSON object of the arguments.")
    body = {"model": _chat_model(), "stream": False, "think": False,
            "messages": [{"role": "system", "content": "Fill in a tool's arguments from what the user asked."},
                         {"role": "user", "content": ask}],
            "options": {"temperature": 0, "num_predict": 120}, "keep_alive": "30m"}
    try:
        text = str((await asyncio.to_thread(lambda: requests.post(_ollama_chat_url(), json=body, timeout=60).json()))
                   .get("message", {}).get("content") or "")
        m = re.search(r"\{.*\}", text, re.S)
        args = json.loads(m.group(0)) if m else None
    except Exception:
        return None
    required = fn["parameters"].get("required", [])
    if not isinstance(args, dict) or any(not str(args.get(k, "")).strip() for k in required):
        return None
    return args


# Refusing to act, by grammar: "I can't / cannot / am unable to <verb>",
# "beyond my tools", "I don't have the ability/access/tools". Not chat
# ("I can't wait", "I can't believe it", "I can't stop laughing").
_REFUSAL = re.compile(
    r"\bi\s+(?:\w+ly\s+|currently\s+|still\s+|just\s+)?(?:can'?t|cannot|am\s+(?:unable|not\s+able)\s+to)\s+"
    r"(?!wait\b|believe\b|stop\b|help\s+(?:but|laughing|smiling)\b)\w+"
    r"|\bbeyond\s+(?:my|the)\s+(?:tools|abilities|capabilities)\b"
    r"|\bdon'?t\s+have\s+(?:the\s+)?(?:ability|access|a\s+way|tools?)\b",
    re.IGNORECASE,
)


async def _yes_no(messages: list[dict]) -> bool:
    """A yes/no judgement from the model. The routing step's own last text is
    left as it was: the code after a judgement still reads it."""
    kept = getattr(_ask_model, "last_text", "")
    try:
        await _ask_model(messages, [], 3)
        return getattr(_ask_model, "last_text", "").strip().lower().startswith("y")
    finally:
        _ask_model.last_text = kept


async def _promised_an_action(said: str) -> bool:
    """The grammar check saw "I'll / let me <verb>"; the model decides whether
    that promises an action on the PC or is just an answer ("let me give you a
    few ideas" is answering, U3b). Only asked when the grammar check fires."""
    ask = [{"role": "system", "content": "You judge what an assistant wrote. Reply with only yes or no."},
           {"role": "user", "content": f"The assistant wrote: \"{said[:400]}\"\nDoes it promise to DO something on "
                                       "the user's computer or in an app (set, open, play, save, remember, send, create, "
                                       "change something)? Answering, explaining, suggesting or giving ideas is not doing."}]
    return await _yes_no(ask)


async def _declined_an_action(said: str, user_text: str) -> bool:
    """The grammar check saw "I can't ..."; the model decides whether that
    declines an action the user asked for (the memory area then learns it) or
    is an ordinary answer that mentions a limit ("I can't see your room, but
    here are some colours", U3b). Only asked when the grammar check fires."""
    ask = [{"role": "system", "content": "You judge what an assistant wrote. Reply with only yes or no."},
           {"role": "user", "content": f"The user said: \"{user_text[:300]}\"\nThe assistant wrote: \"{said[:400]}\"\n"
                                       "Did the user tell the assistant to do an action on the computer or in an app "
                                       "now, and did the assistant say it can't do that action? A question about "
                                       "what the assistant can do or is, or an answer, advice or ideas that mention "
                                       "a limit, is not declining an action."}]
    # Two judges that fail on different things must agree (measured on 18
    # lines): the task/talk one is right on every task and every question about
    # itself but calls advice a task; this one is the opposite.
    return await _wants_a_task(user_text) and await _yes_no(ask)


async def _wants_a_task(user_text: str) -> bool:
    """Is the user's message a task for the PC, a device or an app, or talk
    (a question about MBUDDY itself, ideas, advice, chat)? Live, "can you talk
    out loud?" was sent off to be learned (F1)."""
    ask = [{"role": "user", "content": (
        "Classify what the user said.\nTASK: he wants something done on the computer, on a device, in an app, "
        "or sent to someone, even when he asks politely with \"can you\" or \"could you\".\n"
        "TALK: a question about the assistant itself (what it can do, has or is), a request for ideas, advice "
        f"or an explanation, or chat.\nThe user said: \"{user_text[:300]}\"\nReply with one word: TASK or TALK.")}]
    kept = getattr(_ask_model, "last_text", "")
    try:
        await _ask_model(ask, [], 3)
        return getattr(_ask_model, "last_text", "").strip().upper().startswith("TASK")
    finally:
        _ask_model.last_text = kept


def _one_step_wanted(user_text: str) -> bool:
    try:
        from core import cloud_brain, ui_settings
        if not cloud_brain.active or not ui_settings.get("one_step_routing"):
            return False
    except Exception:
        return False
    # "... on my phone": the phone area is chosen by rule below (the model sent a phone timer to the PC), so it keeps that path
    return not (_PHONE_TARGET.search(user_text) and _PHONE_DOES.search(user_text))


async def _flat_choice(user_text: str, history: list) -> list[dict] | None:
    """The calls from one decision over every tool, or None when the two-step path should decide (not wanted, an error,
    or no tool chosen while the model's own words promise or refuse an action)."""
    if not _one_step_wanted(user_text):
        return None
    from core import timeline
    tools = [t for area in AREAS if area != "abilities" for t in area_tools(area)]
    names = {t["function"]["name"] for t in tools}
    if not tools:
        return None
    try:
        timeline.mark("decide flat>")
        calls = await _ask_model(build_messages(user_text, history, step=2), tools, 240)
        timeline.mark("decide flat<")
    except Exception as exc:  # noqa: BLE001
        log.warn(f"[Agent] one-step choice failed ({type(exc).__name__}); using two steps")
        return None
    calls = [c for c in calls if c.get("name") in names]
    if calls:
        log.info(f"[Agent] one step: {[c['name'] for c in calls]}")
        return calls
    said = getattr(_ask_model, "last_text", "")
    from .llm_router import _ACTION_PROMISE
    if said and (_ACTION_PROMISE.search(said) or _REFUSAL.search(said)):
        log.info(f"[Agent] one step gave words that promise or refuse ({said[:50]!r}): two steps decide")
        return None
    log.info("[Agent] one step: nothing to do")
    return []


async def decide_calls(user_text: str, history: list, question_only: bool = False) -> list[dict]:
    """The model's calls for this turn, nothing run: step 1 picks the area(s),
    step 2 picks among that area's tools. Raises if the model can't be
    reached. tools/bench_toolbox.py and tools/bench_intent.py measure this."""
    step1 = area_tools_step1()
    if not step1:
        return []
    # With the cloud brain one call can choose among every tool at once, instead of an area first and then its tool: a tool turn
    # costs one decision call less (measured on the repo's routing cases: 2.49 s to 1.23 s). When that single call gives no tool
    # and its words promise or refuse something, the two-step path below runs as before, so those cases keep their safeguards.
    flat_first = await _flat_choice(user_text, history)
    if flat_first is not None:
        return flat_first
    # The message decides; earlier messages only resolve what it refers to.
    # Seen with them, "You're the best." after "Play some jazz." played jazz
    # again, and a repeated request looked done already (S99, S109).
    from core import timeline
    timeline.mark("decide step 1>")
    first = await _ask_model(build_messages(user_text, [], step=1), step1)
    timeline.mark("decide step 1<")
    if not [c for c in first if c.get("name") in AREAS] and history and _REFERS_BACK.search(user_text):
        first = await _ask_model(build_messages(user_text, history, step=1), step1)
    areas = [a for a in dict.fromkeys(str(c.get("name", "")) for c in first) if a in AREAS]
    # "... on my phone": the user named where it is for. A countdown on the PC is then the wrong answer, so the phone area is
    # used (the model had sent "put a 5 minute timer on my phone" to the PC's timer).
    if _PHONE_TARGET.search(user_text) and _PHONE_DOES.search(user_text) and "phone" in AREAS and "phone" not in areas:
        areas = [a for a in areas if a != "time"] + ["phone"]
        first = [c for c in first if c.get("name") != "time"] + [{"name": "phone", "args": {"request": user_text[:80]}}]
    # The routing step never answers. When its text refuses to act ("I can't
    # control lights"), the model has decided no area can do it: that is the
    # brain area's job, which learns it (S102). Chat text isn't a refusal.
    # The routing step promised an action in text instead of routing ("I'll
    # remember that your sister's name is Lina"): it decided something needs
    # doing, so it is asked once to route it (S104).
    from .llm_router import _ACTION_PROMISE
    said = getattr(_ask_model, "last_text", "")
    if not areas and said and _ACTION_PROMISE.search(said) and await _promised_an_action(said):
        log.info(f"[Agent] step 1 promised in text ({said[:60]!r}): asking it to route")
        # From this message, like the first pass: earlier ones only when it
        # refers back (live: the retry routed the PREVIOUS turn's topic, U3b).
        context = history if _REFERS_BACK.search(user_text) else []
        retry = build_messages(user_text, context, step=1) + [
            {"role": "assistant", "content": said},
            {"role": "user", "content": "Don't answer: call the area that does it."}]
        first = await _ask_model(retry, step1)
        areas = [a for a in dict.fromkeys(str(c.get("name", "")) for c in first) if a in AREAS]
    refused_text = getattr(_ask_model, "last_text", "")
    if not areas and "memory" in {t["function"]["name"] for t in step1} \
            and _REFUSAL.search(refused_text) and await _declined_an_action(refused_text, user_text):
        log.info(f"[Agent] step 1 said it can't ({refused_text[:60]!r}): the memory area learns it")
        areas = ["memory"]
    for c in first:
        if c.get("name") in AREAS:
            log.info(f"[Agent] routed to {c['name']}: {str(c.get('args', {}).get('request', ''))[:80]!r}")
    if not areas:
        return []
    tools = [t for a in areas for t in area_tools(a)]
    log.info(f"[Agent] area {areas}: {[t['function']['name'] for t in tools]}")
    routed = "; ".join(str(c.get("args", {}).get("request", "")).strip() for c in first
                       if c.get("name") in areas and str(c.get("args", {}).get("request", "")).strip())
    timeline.mark("decide step 2>")
    calls = await _ask_model(build_messages(user_text, history, step=2, routed=routed), tools, 240) if tools else []
    timeline.mark("decide step 2<")
    calls = [c for c in calls if c.get("name") not in AREAS]
    # Step 2 refused in text although its area was chosen for this: ask once
    # to use the tool, which learns what it doesn't know yet and is honest if
    # it can't (live: "design a car in <app>" -> "I can't do that yet", S106).
    refused = getattr(_ask_model, "last_text", "")
    if not calls and len(tools) == 1 and _REFUSAL.search(refused) and tools[0]["function"]["name"] != "learn_about":
        # Step 1 already decided this area is needed; with a single tool, step
        # 2's job is only its arguments. The tool learns what it doesn't know
        # and is honest if it can't (live: "I can't design a car in <app>").
        args = await _fill_args(tools[0], user_text, routed)
        if args is not None:
            log.info(f"[Agent] step 2 refused ({refused[:60]!r}): calling {tools[0]['function']['name']} with {args}")
            calls = [{"name": tools[0]["function"]["name"], "args": args}]
    if not calls and "learn_about" in {t["function"]["name"] for t in tools} \
            and _REFUSAL.search(getattr(_ask_model, "last_text", "")):
        request = next((str(c.get("args", {}).get("request", "")) for c in first if c.get("name") in areas), "")
        topic = (request or user_text).strip()[:80]
        log.info(f"[Agent] step 2 said it can't: learning about {topic!r} instead")
        calls = [{"name": "learn_about", "args": {"topic": topic, "why": user_text[:160]}}]
    if not calls:
        log.info(f"[Agent] step 2 chose nothing in {areas}; it said: {getattr(_ask_model, 'last_text', '')[:140]!r}")
    if question_only:
        calls = drop_writes_for_a_question(calls)
    return calls


async def run_agent(user_text: str, history: list, memory: dict | None,
                    question_only: bool = False) -> list[tuple[str, dict, str]]:
    """Ask the model what to do, run it, return (tool, args, result) tuples."""
    t = time.perf_counter()
    try:
        calls = await decide_calls(user_text, history, question_only)
    except Exception as exc:
        log.warn(f"[Agent] model call failed, answering without tools: {exc}")
        return []
    calls = drop_added_actions(calls, user_text)
    if not question_only and not any(c.get("name") == "add_task" for c in calls):
        item = ran_out_of(user_text)
        if item:
            log.info(f"[Agent] ran out of something -> add_task({item!r})")
            calls.append({"name": "add_task", "args": {"title": f"Buy {item}"}})
    if not any(c.get("name") == "adjust_volume" for c in calls):
        followup = volume_followup_call(user_text)
        if followup:
            log.info(f"[Agent] follow-up to the last volume change: {followup['args']}")
            calls.append(followup)
    if not calls:
        log.info(f"[Agent] no action ({time.perf_counter() - t:.2f}s; {getattr(_ask_model, 'last', '')})")
        return []
    decided = time.perf_counter()
    from core import timeline
    timeline.mark("tool run>")
    results = await asyncio.to_thread(execute_calls, calls, memory)
    timeline.mark("tool run<")
    # Split, so a slow turn says whether choosing or running the tool took
    # the time (S6b: a web search's own seconds looked like a slow model).
    log.info(f"[Agent] ran {[r[0] for r in results]} ({time.perf_counter() - t:.2f}s: deciding "
             f"{decided - t:.2f}s, running {time.perf_counter() - decided:.2f}s)")
    # What was asked and what came back (L10: without this, "which song did
    # the model see?" or "which date did it pass?" couldn't be answered).
    for name, args, result in results:
        kind = "FAILED" if isinstance(result, ToolFailure) else "ok"
        log.info(f"[Agent]   {name}({json.dumps(args, ensure_ascii=False)[:160]}) -> {kind}: {str(result)[:220]}")
    return results
