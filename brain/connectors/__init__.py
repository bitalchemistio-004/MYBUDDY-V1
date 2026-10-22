"""
brain/connectors - local connectors to other apps, as registry entries.

Each connector module exposes ABILITIES: a list of Ability entries with the
same fields as brain/pc_actions.Ability (name, description, risk, topic,
execute, params, required, prepare, spec()), so the lead can add them to
pc_actions.REGISTRY as they are and pc_actions' gate and list_abilities
treat them like its own. Risk follows pc_actions:

- read: only looks; runs at once.
- low: small and easy to undo; runs at once, and the result is checked.
- critical: stored as pending by pc_actions and only the user's yes runs it;
  needs `prepare(args) -> (question, text, plan)`.

Every action entry also has `preview(args)`: what it would do, in
code-written words. `prepare_from_preview()` turns that into a pc_actions
`prepare` if the lead (or the user's settings) wants a low entry to ask first.

Routing for a small model (see docs/research/connectors_plan.md): the model
first sees only the topic list (one line each), then only the tools of the
topic it picks. `categories()` and `abilities_in()` feed that two-step flow.

Everything is local: no cloud APIs, no accounts, nothing is sent anywhere.
Failures are ToolFailure texts starting with "FAILED:", like pc_actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from brain.tools import ToolFailure

RISKS = ("read", "low", "critical")

# One line per topic: what the model sees in step one.
CATEGORIES: dict[str, str] = {
    "clipboard": "Read what the user copied, or put text on the clipboard.",
    "calendar": "The user's local calendar: list what's coming, export it to an .ics file, or import an .ics file.",
    "music": "What's playing, and playing a song or artist in the music app the user chose; setting which app that is.",
    "office": "Writing text into Microsoft Word and saving it.",
    "routines": "Run one of the user's saved routines (steps they made) by name.",
    "brain": "MBUDDY's own notes: looking things up in them, and learning new things into them.",
    "create": "Doing things inside the apps the user connected, after learning how to drive them.",
    "day": "The user's day: plan it around the calendar and tasks, how long they spent in each app, find something across tasks, calendar and notes.",
    "habits": "Habits and their streaks, and the apps that count as a distraction during a focus sprint.",
    "cloud": "A much bigger model in the cloud: hard questions, exact reasoning, long documents, and looking at the clipboard, a file or the screen.",
}


def fail(message: str) -> ToolFailure:
    return ToolFailure(f"FAILED: {message}")


@dataclass
class Ability:
    name: str
    description: str                            # one line, written for the model
    risk: str                                   # "read" | "low" | "critical"
    topic: str                                  # a key of CATEGORIES
    execute: Callable[[dict], str]
    params: dict = field(default_factory=dict)  # JSON-schema properties
    required: list = field(default_factory=list)
    prepare: Callable[[dict], object] | None = None   # critical only (pc_actions contract)
    preview: Callable[[dict], str] | None = None      # "open YouTube Music and search for 'x'"
    # Its result is the whole reply (e.g. "that's new to me, I'll come back"):
    # the model adds nothing (live, it repeated the same line, S116).
    says_all: bool = False

    def __post_init__(self) -> None:
        if self.risk not in RISKS:
            raise ValueError(f"{self.name}: risk must be one of {RISKS}, not {self.risk!r}")
        if self.topic not in CATEGORIES:
            raise ValueError(f"{self.name}: unknown topic {self.topic!r}")
        if self.risk != "read" and self.preview is None:
            raise ValueError(f"{self.name}: an action needs a preview so it can be described before it runs")
        if self.risk == "critical" and self.prepare is None:
            self.prepare = prepare_from_preview(self)   # pc_actions only runs critical entries after a yes

    def spec(self) -> dict:
        """brain/agent.py TOOLS format, same as pc_actions.Ability.spec()."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": list(self.required)},
        }}

    def describe(self, args: dict | None = None) -> str:
        """What it will do, in code-written words (never the model's)."""
        return self.preview(args or {}) if self.preview else self.description


def prepare_from_preview(ability: Ability) -> Callable[[dict], tuple[str, str, dict]]:
    """A pc_actions-style prepare for a low entry that should ask first:
    (question, text, plan); the plan is the args, run unchanged on yes."""
    def prepare(args: dict):
        text = ability.describe(args)
        return (f"Want me to {text}?", text, dict(args))
    return prepare


def _load() -> list[Ability]:
    from brain.connectors import app_driver, calendar_ics, clipboard, cloud, day, learning, music, office, routines
    return [*clipboard.ABILITIES, *calendar_ics.ABILITIES, *music.ABILITIES, *office.ABILITIES,
            *learning.ABILITIES, *app_driver.ABILITIES, *routines.ABILITIES, *day.ABILITIES, *cloud.ABILITIES]


def all_abilities() -> list[Ability]:
    return _load()


def get(name: str) -> Ability | None:
    return next((a for a in _load() if a.name == name), None)


def categories() -> dict[str, str]:
    """Step one of routing: topic -> one-line description."""
    return dict(CATEGORIES)


def abilities_in(topic: str) -> list[Ability]:
    """Step two: only the chosen topic's abilities (a few tools, not dozens)."""
    return [a for a in _load() if a.topic == topic]


def check_args(ability: Ability, args: Any) -> str | None:
    """A reason the arguments don't fit the schema, or None. Small models
    sometimes drop required fields or send the wrong type."""
    if not isinstance(args, dict):
        return "the arguments weren't an object"
    for key in ability.required:
        if args.get(key) in (None, ""):
            return f"'{key}' is required"
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    for key, value in args.items():
        spec = ability.params.get(key)
        if spec is None:
            continue
        want = types.get(spec.get("type", ""))
        if want and not isinstance(value, want):
            return f"'{key}' should be a {spec['type']}"
        if "enum" in spec and value not in spec["enum"]:
            return f"'{key}' must be one of {', '.join(map(str, spec['enum']))}"
    return None


def run(name: str, args: dict | None = None) -> str:
    """Run a read/low entry directly (standalone use and tests). Once the
    entries are in pc_actions.REGISTRY, pc_actions.run is the one to call."""
    ability = get(name)
    if ability is None:
        return fail(f"there is no connector ability called {name}.")
    if ability.risk == "critical":
        return fail(f"{name} must go through the pc_actions gate, which asks the user first.")
    args = dict(args or {})
    problem = check_args(ability, args)
    if problem:
        return fail(f"{name}: {problem}.")
    try:
        return ability.execute(args)
    except Exception as exc:  # the reply must say it failed, not guess
        return fail(f"{name} hit an error: {exc}")
