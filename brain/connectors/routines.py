"""
Routines connector: run one of the user's saved routines by name (core/routines.py).

A routine only runs what the user put in it. The ability starts it in the
background and answers at once; the steps show as a job (the buddy's status
bubble) and the end is announced with the real result.
"""
from __future__ import annotations

from brain.connectors import Ability, fail
from core import routines


def run_routine(args: dict) -> str:
    name = str(args.get("name", "")).strip()
    r = routines.find_by_name(name)
    if r is None:
        known = ", ".join(x["name"] for x in routines.routines()) or "none saved yet"
        return fail(f"I have no routine called \"{name}\". The user's routines: {known}. "
                    "They make one on the Routines page.")
    if not routines.run_async(r["id"]):
        return fail(f"The routine {r['name']} is already running.")
    return f"Started the routine {r['name']} ({len(r['steps'])} steps). I'll tell you how it went."


ABILITIES = [
    Ability(
        name="run_routine",
        topic="routines",
        description="Run one of the user's saved routines (a set of steps they made) by its name.",
        params={"name": {"type": "string", "description": "the routine's name, as the user said it"}},
        required=["name"],
        risk="low",
        execute=run_routine,
        preview=lambda args: f"run your routine \"{str(args.get('name', '')).strip()}\"",
    ),
]
