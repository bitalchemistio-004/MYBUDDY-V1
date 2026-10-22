"""
core/ui_settings.py - the app's settings: one validated store.

Every setting here changes real behaviour somewhere (see
docs/PHASE3_BUILD_PLAN.md, "Settings and what each one controls"); nothing
is decorative. The app reads and writes them through GET/POST /ui/settings,
and backend code reads them with `get()` at the moment it acts, so a change
takes effect on the next turn without a restart.

Stored in ~/.buddy/config/app_settings.json, next to the existing
companion settings. Unknown keys and invalid values are rejected, never
stored.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

# The drawn cast (shell/ui/assets/buddy/chars). The first one is the default.
CHARACTERS = ("blink", "void", "glitch", "cinder", "oracle")
THEMES = ("clay", "character")
# Where the floating character starts. It floats free, with no container; the
# older "island" (a pill at the top centre with a copy of the character in it)
# assumed a notch and is gone: a saved "island" opens as the default.
POPUP_STYLES = ("corner", "edge", "notch")
BUDDY_SIZES = ("small", "medium", "large")
BUDDY_SIDES = ("right", "left")
TOOL_NAMES = ("volume", "timers", "search", "memory", "windows")
TOOL_LEVELS = ("act", "off")
WORK_PLACES = ("pc", "phone")
BRAIN_MODES = ("local", "cloud")
SPEECH_DEVICES = ("auto", "cpu")      # hearing and voice: the graphics card when there is one (faster), or only the processor
# The notch's own settings (buddy/notch.js): how it looks and behaves. Every key has a fixed set of choices.
NOTCH_CHOICES = {
    "glow": ("mood", "character", "off"),       # glow colour follows the moment / always the character's / no glow
    "length": ("compact", "short", "standard", "long"),    # how long the resting pill is; compact: just the face and a dot, wider only to show text
    "thickness": ("thin", "regular"),
    "corners": ("pill", "soft"),
    "border": ("line", "none"),
    "face": ("eyes", "whole", "none"),          # what the resting pill shows of the character; none: just the text field
}
NOTCH_BOOLS = ("clock", "word", "idle_dim", "micro", "breathing")
NOTCH_ACTIONS = ("timer", "remind", "task", "focus", "music", "mic", "quiet", "app", "task2", "calendar", "learned", "phone")
CLOUD_KEYS = ("enabled", "model", "vision_model", "brain_model", "daily_token_cap")
PROFILE_KEYS = ("about_me", "companion_style")  # free text the user writes (S83)
PROFILE_MAX = 800

# The groups of PC abilities the user can switch off in Extensions (S93), with
# the words the app shows for each: one per `topic` of the abilities in
# brain/pc_actions.py REGISTRY, except that the "power" abilities follow
# "system" and "discovery" (list_abilities) has no switch.
EXTENSION_INFO: dict[str, tuple[str, str]] = {
    "apps": ("Apps", "Find and open the apps installed on this PC."),
    "web": ("Web pages", "Open a Google search or a web address in your browser."),
    "files": ("Files", "Write notes and documents into Documents\\MBUDDY, and read, list or open your files. "
                       "Deleting asks you first."),
    "settings": ("Windows settings", "Open a Windows Settings page, like Display, Sound or Bluetooth."),
    "system": ("System", "Screen brightness, closing a frozen app and locking the PC. It asks you first, every time."),
    "music": ("Music", "Play a song, an artist or a kind of music in the music app you chose, and tell you what's playing."),
    "calendar": ("Calendar", "List your upcoming reminders, and export or import .ics calendar files. "
                             "Importing asks you first."),
    "clipboard": ("Clipboard", "Read what you copied when you ask. Copying text for you asks first."),
    "office": ("Word", "Write text into Microsoft Word, and save it as a new file in Documents\\MBUDDY."),
    "brain": ("Brain", "Check its notes in your Obsidian vault every turn, and learn new things online into them."),
    "create": ("Use connected apps", "Do things inside the apps you connected, after learning how to drive each one."),
    "routines": ("Routines", "Run your saved routines (sequences of actions)."),
    "phone": ("Phone actions", "Put an alarm, a timer, a calendar event, a call, a message or directions on your phone as a button you tap there. Nothing happens on the phone by itself."),
    "messages": ("Messages and screen reading", "Tell you which messages are waiting (only the apps you chose in Settings, Messages) and read what a window on screen shows when you ask. It only reads: nothing is clicked or typed."),
    "day": ("Your day", "Plan your day around the calendar and tasks, report your time in apps, and find things across tasks, calendar and notes. It only reads."),
    "habits": ("Habits", "Count the habits you tell me about and keep a distraction list for focus sprints. It only counts and tells you; it closes nothing."),
    "cloud": ("Bigger model", "Ask a much bigger model in the cloud for hard questions and long documents. Anything from this PC (clipboard, a file, the screen) is sent only after you say yes."),
}
EXTENSION_TOPICS = tuple(EXTENSION_INFO)
EXTENSION_ALIASES = {"power": "system"}  # an ability topic -> the switch that governs it
# Apps the user listed in Extensions: display name -> False when MBUDDY must
# leave that app alone. Replaced whole on each update (removing an app is
# sending the list without it).
APP_RULES_MAX = 200
CONNECTED_MAX = 100
APP_NAME_MAX = 60

# Shown as placeholder text for `companion_style`, never stored for the user.
# Distilled from the companion prompt of the previous brain
# (brain/personality.py IDENTITY_CORE at 7e8ae50 and 01f3abe).
COMPANION_STYLE_SUGGESTION = (
    "Be a companion first and a helper second: notice how I'm doing before you solve the problem. "
    "Talk like a friend who knows me well, short and natural, never like a corporate assistant. "
    "Be honest and a little opinionated; tease me lightly when it fits, and use humour to take the "
    "edge off stress. When I'm busy or debugging, be calm, direct and practical. When I'm tired, "
    "keep it gentle. Cheer me on when things go well and remind me of my goals when they get hard. "
    "Don't end every reply with a question."
)

DEFAULTS: dict[str, Any] = {
    "character": "blink",
    "theme": "character",    # the app wears the chosen buddy's look; "clay" is the plain one
    # The buddy stands in the bottom-right corner above the taskbar (the notch looked cheap and sat over the apps), and only
    # while the app itself is closed: "closed" (default) or "always".
    "popup_style": "corner",
    "buddy_when": "closed",
    "show_buddy": True,
    "buddy_size": "medium",
    "buddy_side": "right",
    # Where the user dragged the floating character: {"x", "y"} in screen
    # pixels and "for", the style:side it was dragged in (another style or
    # side starts from its own place again). {} = not dragged.
    "buddy_position": {},
    "one_step_routing": True,  # with the cloud brain, one decision over every tool instead of an area and then its tool (brain/agent._flat_choice)
    "awareness": True,        # MBUDDY knows what is in front, today's time per app, the next event (brain/awareness.py); off: it is told none of it
    "instant_replies": True,  # simple commands (time, timer, volume...) are answered by code at once, without the model
    "notch_hover": True,    # the notch opens when the pointer rests on it (a click always opens it)
    "buddy_effects": True,   # the light effects around the character
    "user_name": "",         # asked in the first-run setup; empty until then (prompts say "the user")
    "microphone": "",
    "microphone_on": True,  # off: the mic is released and nothing is heard
    "reduced_motion": False,
    "speak_replies": True,
    "expression_sounds": True,
    "voice": "af_heart",
    "emotion_strength": 60,
    "internet_search": True,
    "home_location": "",
    "llm_model": "qwen3:4b-instruct",
    "tool_permissions": {name: "act" for name in TOOL_NAMES},
    "extensions": {topic: True for topic in EXTENSION_TOPICS},  # PC ability groups (S93)
    "app_rules": {},         # app name -> False: MBUDDY leaves that app alone (S93)
    "connected_apps": [],    # [{"name", "path", "added"}]: apps the user connected by browsing (S103)
    # The bigger model in the cloud (core/cloud.py): off switch, which models, and a daily token cap (0 = none).
    "cloud": {"enabled": True, "model": "gpt-oss:120b", "vision_model": "gemma4:31b", "brain_model": "gemma4:31b", "daily_token_cap": 0},
    # Where the everyday thinking runs: the small model on this PC ("local") or a cloud model standing in
    # for it ("cloud", for weak laptops). Read once when MBUDDY starts (core/cloud_brain.py).
    "brain_mode": "local",
    # Where hearing (speech recognition) and the voice run. "cpu" keeps the graphics card free; read when MBUDDY starts.
    "speech_device": "auto",
    # Messages that are waiting for you (core/notif_watch.py): off until the user switches it on.
    "notify": {"enabled": False, "apps": [], "chats": [], "muted": [], "keywords": ["urgent", "asap", "emergency", "call me", "deadline", "important", "now"],
               "remind_after_min": 10, "important_after_min": 2, "repeat_every_min": 15, "max_reminders": 3, "model_judge": True, "share_with_cloud": False},
    # The notch's look and behaviour (see NOTCH_CHOICES). `colour` is "" or #rrggbb (used when glow is "character");
    # `buttons` are the tools shown in its room; `gap` is the space above it in px.
    "notch": {"glow": "mood", "glow_strength": 60, "colour": "", "length": "compact", "thickness": "thin", "corners": "pill", "border": "line",
              "face": "eyes", "clock": True, "word": True, "idle_dim": False, "micro": True, "breathing": False, "gap": 6,
              "buttons": list(NOTCH_ACTIONS)},
    # Where the user is working: on the PC (the buddy speaks and listens here) or on the phone
    # (the PC stays quiet; the phone gets the replies and the approvals).
    "work_on": "pc",
    "do_not_disturb": False,
    # Free the model from the GPU after this many minutes asleep (it is loaded
    # again on wake); 0 = never. The user chose 10 (2026-09-29, study 9.2).
    "unload_model_after_min": 10,
    "notifications": True,
    "start_with_windows": False,
    "onboarding_done": False,
    "about_me": "",          # who the user is, in their words (brain/user_profile.py)
    "companion_style": "",   # how they want MBUDDY to be with them
}


class SettingsError(ValueError):
    """A setting name or value that isn't allowed; the message says why."""


def _path() -> Path:
    override = os.getenv("MBUDDY_SETTINGS_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "config" / "app_settings.json"


def _check(key: str, value: Any, current: dict[str, Any]) -> Any:
    """The value to store for `key` (given the `current` settings), or SettingsError."""
    if key not in DEFAULTS:
        raise SettingsError(f"Unknown setting: {key}")
    default = DEFAULTS[key]
    if key == "character":
        if value not in CHARACTERS:
            raise SettingsError(f"character must be one of {', '.join(CHARACTERS)}")
    elif key == "theme":
        if value not in THEMES:
            raise SettingsError(f"theme must be one of {', '.join(THEMES)}")
    elif key == "popup_style":
        if value not in POPUP_STYLES:
            raise SettingsError(f"popup_style must be one of {', '.join(POPUP_STYLES)}")
    elif key == "buddy_when":
        if value not in ("closed", "always"):
            raise SettingsError("buddy_when must be closed or always")
    elif key == "buddy_size":
        if value not in BUDDY_SIZES:
            raise SettingsError(f"buddy_size must be one of {', '.join(BUDDY_SIZES)}")
    elif key == "buddy_side":
        if value not in BUDDY_SIDES:
            raise SettingsError(f"buddy_side must be one of {', '.join(BUDDY_SIDES)}")
    elif key == "buddy_position":
        if value in (None, {}):
            value = {}
        else:
            ok = (isinstance(value, dict) and set(value) == {"x", "y", "for"}
                  and all(isinstance(value[k], int) and not isinstance(value[k], bool) and abs(value[k]) <= 100_000 for k in ("x", "y"))
                  and value["for"] in {f"{style}:{side}" for style in POPUP_STYLES for side in BUDDY_SIDES})
            if not ok:
                raise SettingsError("buddy_position must be empty or {x, y, for} with whole screen pixels")
            value = {"x": value["x"], "y": value["y"], "for": value["for"]}
    elif key == "user_name":
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40 or not all(c.isalpha() or c in " -'" for c in value.strip()):
            raise SettingsError("user_name must be a name (letters, up to 40 characters)")
        value = value.strip()
    elif key == "work_on":
        if value not in WORK_PLACES:
            raise SettingsError(f"work_on must be one of {', '.join(WORK_PLACES)}")
    elif key == "notch":
        if not isinstance(value, dict):
            raise SettingsError("notch must be an object")
        merged = dict(current.get("notch") or DEFAULTS["notch"])
        for name, v in value.items():
            if name in NOTCH_CHOICES:
                if v not in NOTCH_CHOICES[name]:
                    raise SettingsError(f"notch: {name} must be one of {', '.join(NOTCH_CHOICES[name])}")
            elif name in NOTCH_BOOLS:
                if not isinstance(v, bool):
                    raise SettingsError(f"notch: {name} must be true or false")
            elif name == "glow_strength":
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                    raise SettingsError("notch: glow_strength must be a number from 0 to 100")
                v = int(round(v))
            elif name == "gap":
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 24:
                    raise SettingsError("notch: gap must be from 0 to 24 pixels")
                v = int(round(v))
            elif name == "colour":
                if not isinstance(v, str) or (v and not (len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in v[1:]))):
                    raise SettingsError("notch: colour must be empty or a colour like #ff7a2f")
                v = v.lower()
            elif name == "buttons":
                if not isinstance(v, list) or not all(x in NOTCH_ACTIONS for x in v):
                    raise SettingsError("notch: buttons must be a list of the known tools")
                v = [x for x in NOTCH_ACTIONS if x in v]
            else:
                raise SettingsError(f"notch: {name} isn't a notch setting")
            merged[name] = v
        value = merged
    elif key == "speech_device":
        if value not in SPEECH_DEVICES:
            raise SettingsError(f"speech_device must be one of {', '.join(SPEECH_DEVICES)}")
    elif key == "notify":
        if not isinstance(value, dict):
            raise SettingsError("notify must be an object")
        merged = dict(current.get("notify") or DEFAULTS["notify"])
        for name, v in value.items():
            if name not in DEFAULTS["notify"]:
                raise SettingsError(f"notify: {name} isn't a message setting")
            if name in ("enabled", "model_judge", "share_with_cloud"):
                if not isinstance(v, bool):
                    raise SettingsError(f"notify: {name} must be true or false")
            elif name in ("apps", "chats", "muted", "keywords"):
                if not isinstance(v, list) or len(v) > 60 or not all(isinstance(x, str) and 0 < len(x.strip()) <= 60 and x.isprintable() for x in v):
                    raise SettingsError(f"notify: {name} must be a list of short names")
                v = list(dict.fromkeys(x.strip() for x in v))
            else:
                limits = {"remind_after_min": (1, 240), "important_after_min": (1, 60), "repeat_every_min": (1, 240), "max_reminders": (1, 10)}[name]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not limits[0] <= v <= limits[1]:
                    raise SettingsError(f"notify: {name} must be a whole number from {limits[0]} to {limits[1]}")
                v = int(round(v))
            merged[name] = v
        value = merged
    elif key == "brain_mode":
        if value not in BRAIN_MODES:
            raise SettingsError(f"brain_mode must be one of {', '.join(BRAIN_MODES)}")
    elif key == "cloud":
        if not isinstance(value, dict):
            raise SettingsError("cloud must be an object")
        merged = dict(current.get("cloud") or DEFAULTS["cloud"])
        for name, v in value.items():
            if name not in CLOUD_KEYS:
                raise SettingsError(f"cloud: {name} isn't one of {', '.join(CLOUD_KEYS)}")
            if name == "enabled":
                if not isinstance(v, bool):
                    raise SettingsError("cloud: enabled must be true or false")
            elif name == "daily_token_cap":
                if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 50_000_000:
                    raise SettingsError("cloud: daily_token_cap must be whole tokens from 0 (no limit) to 50,000,000")
            else:
                if not isinstance(v, str) or not v.strip() or len(v.strip()) > 60 or not all(c.isalnum() or c in ":.-_/" for c in v.strip()):
                    raise SettingsError(f"cloud: {name} must be a model name such as gpt-oss:120b")
                v = v.strip()
            merged[name] = v
        value = merged
    elif key == "unload_model_after_min":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 240:
            raise SettingsError("unload_model_after_min must be minutes from 0 (never) to 240")
        value = int(round(value))
    elif key == "emotion_strength":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise SettingsError("emotion_strength must be a number from 0 to 100")
        value = int(round(value))
    elif key == "tool_permissions":
        if not isinstance(value, dict):
            raise SettingsError("tool_permissions must be an object")
        merged = dict(current.get("tool_permissions") or DEFAULTS["tool_permissions"])
        for name, level in value.items():
            if name not in TOOL_NAMES or level not in TOOL_LEVELS:
                raise SettingsError(f"tool_permissions: {name} must be one of {', '.join(TOOL_LEVELS)}")
            merged[name] = level
        value = merged
    elif key == "extensions":
        if not isinstance(value, dict):
            raise SettingsError("extensions must be an object")
        merged = dict(current.get("extensions") or DEFAULTS["extensions"])
        for topic, on in value.items():
            if topic not in EXTENSION_TOPICS:
                raise SettingsError(f"extensions: {topic} isn't one of {', '.join(EXTENSION_TOPICS)}")
            if not isinstance(on, bool):
                raise SettingsError(f"extensions: {topic} must be true or false")
            merged[topic] = on
        value = merged
    elif key == "app_rules":
        if not isinstance(value, dict):
            raise SettingsError("app_rules must be an object")
        if len(value) > APP_RULES_MAX:
            raise SettingsError(f"app_rules can hold at most {APP_RULES_MAX} apps")
        rules: dict[str, bool] = {}
        seen: set[str] = set()
        for name, allowed in value.items():
            clean = " ".join(name.split()) if isinstance(name, str) else ""
            if not clean or len(clean) > APP_NAME_MAX or not clean.isprintable():
                raise SettingsError(f"app_rules: an app name must be text of 1 to {APP_NAME_MAX} characters")
            if not isinstance(allowed, bool):
                raise SettingsError(f"app_rules: {clean} must be true or false")
            if _app_key(clean) in seen:
                raise SettingsError(f"app_rules: {clean} is listed twice")
            seen.add(_app_key(clean))
            rules[clean] = allowed
        value = rules
    elif key == "connected_apps":
        if not isinstance(value, list) or len(value) > CONNECTED_MAX:
            raise SettingsError(f"connected_apps must be a list of at most {CONNECTED_MAX} apps")
        apps, seen = [], set()
        for item in value:
            if not isinstance(item, dict):
                raise SettingsError("connected_apps: each app must be an object")
            name = " ".join(str(item.get("name", "")).split())
            path = str(item.get("path", "")).strip()
            if not name or len(name) > APP_NAME_MAX or not name.isprintable():
                raise SettingsError(f"connected_apps: an app name must be text of 1 to {APP_NAME_MAX} characters")
            if not path or len(path) > 500 or not path.isprintable():
                raise SettingsError(f"connected_apps: {name} needs the path of the app")
            if _app_key(name) in seen:
                raise SettingsError(f"connected_apps: {name} is connected twice")
            seen.add(_app_key(name))
            apps.append({"name": name, "path": path, "added": str(item.get("added", ""))[:40]})
        value = apps
    elif key in PROFILE_KEYS:
        if not isinstance(value, str):
            raise SettingsError(f"{key} must be text")
        # Plain text: keep line breaks and tabs, drop other control characters.
        value = "".join(c for c in value.replace("\r\n", "\n").replace("\r", "\n") if c in "\n\t" or c.isprintable()).strip()
        if len(value) > PROFILE_MAX:
            raise SettingsError(f"{key} must be at most {PROFILE_MAX} characters")
    elif isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{key} must be true or false")
    elif isinstance(default, str):
        if not isinstance(value, str) or len(value) > 120:
            raise SettingsError(f"{key} must be text (at most 120 characters)")
        value = value.strip()
        if key in ("voice", "llm_model") and not value:
            raise SettingsError(f"{key} can't be empty")
    return value


class _Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None
        self._loaded_from: Path | None = None
        self._mtime: float | None = None
        self._rev = 0  # goes up whenever the stored settings may have changed

    def _load(self) -> dict[str, Any]:
        path = _path()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        # Reload when another process changed the file: a stale copy written
        # back whole silently reverted other settings (live: the microphone
        # came back "off" after it had been switched on).
        if self._data is not None and self._loaded_from == path and self._mtime == mtime:
            return self._data
        data = copy.deepcopy(DEFAULTS)
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            for key, value in saved.items():
                try:
                    data[key] = _check(key, value, data)
                except SettingsError:
                    pass  # a bad saved value falls back to its default
        except (OSError, ValueError):
            pass
        self._data, self._loaded_from, self._mtime = data, path, mtime
        self._rev += 1
        return data

    def all(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._load())

    def get(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._load()[key])

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise SettingsError("Send at least one setting to change")
        with self._lock:
            data = self._load()
            checked = {}
            for key, value in changes.items():
                checked[key] = _check(key, value, data)  # all or nothing
            data.update(checked)
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
            self._mtime = path.stat().st_mtime
            self._rev += 1
            return copy.deepcopy(data)

    def revision(self) -> int:
        with self._lock:
            self._load()  # picks up a change another process wrote
            return self._rev

    def reset_cache(self) -> None:
        with self._lock:
            self._data, self._loaded_from, self._mtime = None, None, None


_store = _Store()


def all_settings() -> dict[str, Any]:
    return _store.all()


def get(key: str) -> Any:
    # Working on the phone: the PC stays quiet. Every place that decides whether to speak asks for
    # "speak_replies", so this one rule silences them all; the stored switch is untouched and applies
    # again as soon as the user is back on the PC.
    if key == "speak_replies" and _store.get("work_on") == "phone":
        return False
    return _store.get(key)


def update(changes: dict[str, Any]) -> dict[str, Any]:
    return _store.update(changes)


def revision() -> int:
    """A number that changes whenever the settings change (a write here, or
    the file changed by another process) and stays the same otherwise. The
    app windows watch it in /ui/live and refetch their settings (S81)."""
    return _store.revision()


def tool_allowed(name: str) -> bool:
    """False when the user switched this tool off in Extensions."""
    return get("tool_permissions").get(name, "act") != "off"


def extension_for(topic: Any) -> str | None:
    """The Extensions switch that governs a PC-ability topic ("power" ->
    "system"), or None for a topic without one ("discovery", unknown)."""
    name = str(topic or "").strip().lower()
    name = EXTENSION_ALIASES.get(name, name)
    return name if name in EXTENSION_TOPICS else None


def extension_enabled(topic: str) -> bool:
    """False when the user switched this group of PC abilities off in
    Extensions (S93). `topic` is an ability's topic in brain/pc_actions.py;
    "power" follows "system", and a topic without a switch is on. Never
    raises: if the settings can't be read, the defaults (all on) apply, as
    they do for every setting."""
    try:
        key = extension_for(topic)
        return key is None or get("extensions").get(key, True) is not False
    except Exception:
        return True


def _app_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def app_allowed(name: str) -> bool:
    """False when the user switched this app off in Extensions (S93): MBUDDY
    may not act on it. Matched by name, ignoring case and extra spaces; an
    app that isn't in the list is allowed. Never raises (see extension_enabled)."""
    try:
        if not isinstance(name, str) or not name.strip():
            return True
        key = _app_key(name)
        for listed, allowed in get("app_rules").items():
            if _app_key(listed) == key:
                return allowed is not False
        return True
    except Exception:
        return True


def connected_apps() -> list[dict]:
    """The apps the user connected (S103). Never raises."""
    try:
        return [dict(a) for a in get("connected_apps") or []]
    except Exception:
        return []


def connected_app(name_or_path: str) -> dict | None:
    """The connected app with this name (ignoring case and spaces, or the
    name without a version suffix, "Name" for "Name 4.3") or this
    path; None if it isn't connected. Never raises."""
    try:
        text = str(name_or_path or "").strip()
        if not text:
            return None
        key, norm = _app_key(text), os.path.normcase(os.path.normpath(text))
        apps = connected_apps()
        for app in apps:
            if _app_key(app["name"]) == key or os.path.normcase(os.path.normpath(app["path"])) == norm:
                return app
        for app in apps:  # "Name" -> "Name 4.3"
            if _app_key(app["name"]).split(" ")[0] == key.split(" ")[0]:
                return app
        return None
    except Exception:
        return None


USER_MARK = "USER_NAME"


def personalize(text: str, fallback: str = "the user") -> str:
    """Put the name from Settings where the prompts say USER_NAME. No name is built into the app: without one the
    prompts say "the user" ("The user" at a sentence start). Speech hints pass fallback="" and lose the slot."""
    if not text or USER_MARK not in text:
        return text
    import re
    name = (get("user_name") or "").strip()
    if name:
        return re.sub(rf"\b{USER_MARK}\b", name, text)
    if fallback:
        text = re.sub(rf"(^|[.!?:]\s+|\n\s*|\(\s*)\b{USER_MARK}\b", lambda m: m.group(1) + fallback[:1].upper() + fallback[1:], text)
        return re.sub(rf"\b{USER_MARK}\b", fallback, text)
    return re.sub(r"^\s*,\s*|\s*,\s*$", "", re.sub(rf"\s*\b{USER_MARK}\b\s*,?", "", text)).strip()


def reset_cache() -> None:
    """Forget the cached copy (tests, or after editing the file by hand)."""
    _store.reset_cache()
