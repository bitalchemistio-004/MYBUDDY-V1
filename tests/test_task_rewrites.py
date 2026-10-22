"""The task the model writes for an app can't add a critical action the user never said (F55)."""
from brain.agent import drop_added_actions


def _task(calls):
    return [c["args"]["task"] for c in calls]


def test_an_added_close_is_dropped_and_the_rest_kept():
    calls = [{"name": "use_app", "args": {"app": "Blender", "task": "make a tree, and close the extra Blender windows"}}]
    assert _task(drop_added_actions(calls, "make a tree. I got like 3 blenders open lol")) == ["make a tree"]


def test_a_task_that_is_only_an_added_action_is_dropped():
    calls = [{"name": "use_app", "args": {"app": "Blender", "task": "close the extra Blender windows"}}]
    assert drop_added_actions(calls, "I got like 3 blenders open lol") == []


def test_what_the_user_said_stays():
    calls = [{"name": "use_app", "args": {"app": "Blender", "task": "render the chair then close Blender"}}]
    assert _task(drop_added_actions(calls, "render the chair and close blender after")) == ["render the chair then close Blender"]
    work = [{"name": "use_app", "args": {"app": "Blender", "task": "remove the default cube and make a chair"}}]
    assert drop_added_actions(work, "make a chair") == work            # work inside the app isn't critical
    other = [{"name": "window_action", "args": {"action": "close", "target": "Blender"}}]
    assert drop_added_actions(other, "close blender") == other          # other tools have their own confirmation
