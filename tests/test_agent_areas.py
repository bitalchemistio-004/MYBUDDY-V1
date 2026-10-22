"""Two-step tool choice (S96): the model first picks an area, then sees only
that area's tools. No model is called: _ask_model is replaced."""
import asyncio

from brain import agent, pc_actions


def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_every_tool_is_in_exactly_one_area():
    listed = [n for _desc, names in agent.AREAS.values() for n in names]
    assert len(listed) == len(set(listed)), "a tool is in two areas"
    offered = set(pc_actions.REGISTRY) | _names(agent.TOOLS)
    assert offered == set(listed), f"in no area: {offered - set(listed)}; unknown: {set(listed) - offered}"


def test_step_one_offers_one_tool_per_area_described_by_the_catalogue():
    step1 = agent.area_tools_step1()
    names = _names(step1)
    assert names <= set(agent.AREAS) and "music" in names and "memory" in names
    for t in step1:  # described by the area's own catalogue line ({apps} filled in)
        line = agent.describe_area(agent.AREAS[t["function"]["name"]][0])
        assert line.lower() in t["function"]["description"].lower()
        assert set(t["function"]["parameters"]["properties"]) == {"request"}


def test_a_switched_off_tool_leaves_its_area(monkeypatch):
    monkeypatch.setattr(agent, "tool_switched_off", lambda name: name in {"get_weather"})
    assert agent.area_tools("weather") == []
    assert "weather" not in _names(agent.area_tools_step1())


def _fake_model(monkeypatch, first, second):
    seen = []

    async def ask(messages, tools=None, num_predict=120):
        seen.append((messages[0]["content"], _names(tools or [])))
        return first if len(seen) == 1 else second

    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    return seen


def test_step_two_sees_only_the_chosen_areas_tools(monkeypatch):
    seen = _fake_model(monkeypatch, [{"name": "music", "args": {}}],
                       [{"name": "music_play", "args": {"query": "jazz"}}])
    calls = asyncio.run(agent.decide_calls("put on some jazz", []))
    assert calls == [{"name": "music_play", "args": {"query": "jazz"}}]
    assert seen[0][1] == _names(agent.area_tools_step1()) and "area" in seen[0][0]
    assert seen[1][1] == set(agent.AREAS["music"][1]) & (set(pc_actions.REGISTRY) | _names(agent.TOOLS))


def test_no_area_means_no_second_call(monkeypatch):
    seen = _fake_model(monkeypatch, [], [{"name": "remember", "args": {"fact": "x"}}])
    assert asyncio.run(agent.decide_calls("I'm bored", [])) == [] and len(seen) == 1


def test_an_unknown_area_is_ignored(monkeypatch):
    seen = _fake_model(monkeypatch, [{"name": "lights", "args": {}}], [])
    assert asyncio.run(agent.decide_calls("turn off the lights", [])) == [] and len(seen) == 1


def test_a_question_still_reaches_its_tools_but_writes_nothing(monkeypatch):
    _fake_model(monkeypatch, [{"name": "schedule", "args": {}}],
                [{"name": "list_tasks", "args": {}}, {"name": "add_task", "args": {"title": "x"}}])
    calls = asyncio.run(agent.decide_calls("is my list still there?", [], question_only=True))
    assert [c["name"] for c in calls] == ["list_tasks"]


def test_a_refusal_in_the_routing_step_goes_to_the_brain_area(monkeypatch):
    # The model answered step 1 with "I can't..." instead of routing: no area
    # can do it, so the brain area (which learns it) gets it (S102).
    seen = []

    async def ask(messages, tools=None, num_predict=120):
        seen.append({t["function"]["name"] for t in tools or []})
        if len(seen) == 1:
            agent._ask_model.last_text = "I cannot control lights or smart home devices."
            return []
        return [{"name": "learn_about", "args": {"topic": "smart lights"}}]
    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    judged = []

    async def judge(messages):   # the model agrees it declined an action (U3b)
        judged.append(messages[-1]["content"])
        return True
    monkeypatch.setattr(agent, "_yes_no", judge)

    async def task(text):        # and that the user asked for a task, not about MBUDDY itself (F1)
        return True
    monkeypatch.setattr(agent, "_wants_a_task", task)
    calls = asyncio.run(agent.decide_calls("Turn off the lights.", []))
    assert judged and "Turn off the lights." in judged[0]
    assert calls == [{"name": "learn_about", "args": {"topic": "smart lights"}}]
    assert seen[1] == {"remember", "search_notes", "learn_about"}


def test_chat_text_in_the_routing_step_is_not_a_refusal(monkeypatch):
    async def ask(messages, tools=None, num_predict=120):
        agent._ask_model.last_text = "I can't wait to hear how it goes!"
        return []
    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    assert asyncio.run(agent.decide_calls("I have a job interview tomorrow.", [])) == []


def test_a_refusal_in_the_learning_area_becomes_learning(monkeypatch):
    # Step 2 was offered learn_about and still wrote "I can't...": learning is
    # what that area does with something it can't do yet (S102).
    calls_made = []

    async def ask(messages, tools=None, num_predict=120):
        calls_made.append(1)
        if len(calls_made) == 1:
            agent._ask_model.last_text = ""
            return [{"name": "memory", "args": {"request": "use Blender"}}]
        agent._ask_model.last_text = "I cannot figure out how to use Blender for you."
        return []
    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    calls = asyncio.run(agent.decide_calls("I don't know how to use Blender, can you figure it out?", []))
    assert calls == [{"name": "learn_about", "args": {"topic": "use Blender",
                                                       "why": "I don't know how to use Blender, can you figure it out?"}}]


def test_a_refusal_in_a_single_tool_area_fills_that_tools_arguments(monkeypatch):
    # Step 1 chose the area; step 2 wrote "I can't..." instead of calling its
    # only tool: the arguments are filled and the tool (which learns) is called.
    calls = []

    async def ask(messages, tools=None, num_predict=120):
        calls.append(1)
        if len(calls) == 1:
            agent._ask_model.last_text = ""
            return [{"name": "clipboard", "args": {"request": "copy the note"}}]
        agent._ask_model.last_text = "I cannot copy that for you."
        return []

    async def fill(tool, user_text, routed):
        return {"text": "the note"}
    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_fill_args", fill)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    monkeypatch.setattr(agent, "area_tools", lambda area: [
        {"type": "function", "function": {"name": "clipboard_write", "description": "d",
                                          "parameters": {"type": "object", "properties": {"text": {}}, "required": ["text"]}}}]
        if area == "clipboard" else [])
    monkeypatch.setattr(agent, "area_tools_step1", lambda: [{"type": "function", "function": {"name": "clipboard"}}])
    assert asyncio.run(agent.decide_calls("copy the note", [])) == [{"name": "clipboard_write", "args": {"text": "the note"}}]


def test_an_answer_that_mentions_a_limit_or_says_let_me_is_not_routed(monkeypatch):
    # U3b: "I can't see your room, but here are some colours" and "let me give
    # you a few ideas" are answers. The grammar check fires, the model judges,
    # and nothing is routed, learned or searched.
    for text in ("I can't see your room, but warm neutrals are a safe choice.",
                 "Let me give you a few ideas for tonight: pasta, a stir-fry, or soup."):
        calls_made = []

        async def ask(messages, tools=None, num_predict=120, text=text):
            calls_made.append(1)
            agent._ask_model.last_text = text
            return []

        async def judge(messages):
            return False
        monkeypatch.setattr(agent, "_ask_model", ask)
        monkeypatch.setattr(agent, "_yes_no", judge)
        monkeypatch.setattr(agent, "_wants_a_task", judge)
        monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
        assert asyncio.run(agent.decide_calls("Help me pick a colour for my bedroom walls.", [])) == []
        assert len(calls_made) == 1      # no retry, no second step


def test_a_promise_is_routed_from_this_message_not_the_previous_one(monkeypatch):
    # U3b, live: after "a fun fact about space", "what should I cook tonight?"
    # was asked to route and searched the space topic. The retry sees earlier
    # messages only when this one refers back.
    seen = []

    async def ask(messages, tools=None, num_predict=120):
        seen.append(" ".join(str(m.get("content", "")) for m in messages))
        agent._ask_model.last_text = "I'll set that up for you." if len(seen) == 1 else ""
        return []

    async def judge(messages):
        return True
    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_yes_no", judge)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    history = [{"role": "user", "content": "tell me a fun fact about space"},
               {"role": "assistant", "content": "Space is silent."}]
    asyncio.run(agent.decide_calls("set up a reminder for the dentist", history))
    assert len(seen) == 2 and "fun fact about space" not in seen[1]


def test_a_question_about_itself_is_never_sent_off_to_be_learned(monkeypatch):
    # F1, live: "can you talk out loud?" -> "That's new to me. Give me a little
    # time to learn about can you talk out loud". Its own answer mentioned a
    # limit ("I can't hear..."), which looked like a refusal.
    made = []

    async def ask(messages, tools=None, num_predict=120):
        made.append(1)
        agent._ask_model.last_text = "Yes, I can speak out loud. The microphone is off, so I can't hear the room."
        return []

    async def talk(text):
        return False             # the model classifies the message as talk, not a task

    async def would_say_yes(messages):
        return True
    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_wants_a_task", talk)
    monkeypatch.setattr(agent, "_yes_no", would_say_yes)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    assert asyncio.run(agent.decide_calls("Can you speak to people in the room?", [])) == []
    assert len(made) == 1


# ── one decision over every tool, with the cloud brain ──
def _cloud(monkeypatch, on=True):
    from core import cloud_brain
    monkeypatch.setattr(cloud_brain, "active", on)


def test_one_step_with_the_cloud_brain_makes_one_call_for_a_tool(monkeypatch):
    _cloud(monkeypatch)
    seen = _fake_model(monkeypatch, [{"name": "music_play", "args": {"query": "jazz"}}], [])
    calls = asyncio.run(agent.decide_calls("put on some jazz", []))
    assert calls == [{"name": "music_play", "args": {"query": "jazz"}}]
    assert len(seen) == 1 and "music_play" in seen[0][1] and "set_timer" in seen[0][1]      # every tool in one call, no area step


def test_one_step_chat_is_one_call_and_no_tool(monkeypatch):
    _cloud(monkeypatch)
    seen = _fake_model(monkeypatch, [], [])
    assert asyncio.run(agent.decide_calls("what's a good name for a cat", [])) == [] and len(seen) == 1


def test_one_step_falls_back_to_two_steps_when_its_words_promise_an_action(monkeypatch):
    _cloud(monkeypatch)
    seen = []

    async def ask(messages, tools=None, num_predict=120):
        seen.append(len(seen))
        if len(seen) == 1:
            ask.last_text = "I'll remember that your sister's name is Lina."
            return []
        ask.last_text = ""
        return [{"name": "memory", "args": {"request": "sister"}}] if len(seen) == 2 else [{"name": "remember", "args": {"fact": "x"}}]
    ask.last_text = ""
    monkeypatch.setattr(agent, "_ask_model", ask)
    monkeypatch.setattr(agent, "_state_line", lambda: "Now: test.")
    asyncio.run(agent.decide_calls("my sister is called Lina", []))
    assert len(seen) >= 2                                                                   # the two-step path ran after the promise


def test_one_step_keeps_the_phone_rule_and_is_off_without_the_cloud_brain(monkeypatch):
    _cloud(monkeypatch)
    seen = _fake_model(monkeypatch, [{"name": "time", "args": {}}], [{"name": "phone_do", "args": {"kind": "timer"}}])
    asyncio.run(agent.decide_calls("put a 5 minute timer on my phone", []))
    assert len(seen) == 2                                                                   # the existing two-step phone routing
    _cloud(monkeypatch, on=False)
    seen2 = _fake_model(monkeypatch, [{"name": "music", "args": {}}], [{"name": "music_play", "args": {"query": "jazz"}}])
    asyncio.run(agent.decide_calls("put on some jazz", []))
    assert len(seen2) == 2
