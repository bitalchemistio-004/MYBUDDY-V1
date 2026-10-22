"""wardrobe.py - what the character wears, and what the user has earned the right to wear.

Decorations are unlocked by progress the app really saw (core/rewards.py: level, tasks done, focus sprints, habits kept, best
streak). Nothing is sold and nothing is random. What is worn is stored on this PC (~/.buddy/wardrobe.json) and is only the
user's choice: an item that is not unlocked can never be worn, and the live state never reports one.

Each of the five characters has a wardrobe of its own, drawn for its look (Blink's first; Void, Glitch, Cinder and Oracle have
their own items). What is worn is kept per character, so choosing another buddy brings back what it wore. The catalog is the
contract with the drawing in shell/ui/js/buddy/wardrobe.js: the ids here are the drawn item ids there.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

CHARACTERS = ("blink", "void", "glitch", "cinder", "oracle")
CHARACTER = "blink"                      # the default, and the one whose items are ITEMS below
SLOTS = ("crown", "eyes", "neck", "back", "hand", "aura")

# slot -> (id, name, progress key, amount needed, how it is earned)
ITEMS: dict[str, tuple[tuple[str, str, str, int, str], ...]] = {
    "crown": (("crown", "Crown", "streak", 7, "Keep a 7-day streak"), ("halo", "Halo", "level", 6, "Reach level 6"),
              ("sprout", "Sprout", "habits", 30, "Keep habits 30 times"), ("antenna", "Antenna", "level", 2, "Reach level 2"),
              ("horns", "Horns", "sprints", 10, "Finish 10 focus sprints"), ("beanie", "Beanie", "level", 4, "Reach level 4"),
              ("party", "Party cone", "level", 3, "Reach level 3"), ("wizard", "Wizard hat", "tasks", 100, "Complete 100 tasks")),
    "eyes": (("glasses", "Glasses", "habits", 10, "Keep habits 10 times"), ("shades", "Shades", "level", 5, "Reach level 5"),
             ("monocle", "Monocle", "tasks", 100, "Complete 100 tasks"), ("blush", "Blush", "level", 2, "Reach level 2"),
             ("freckles", "Freckles", "streak", 5, "Keep a 5-day streak"), ("scan", "Visor scan", "sprints", 5, "Finish 5 focus sprints"),
             ("brows", "Brows", "level", 3, "Reach level 3")),
    "neck": (("scarf", "Focus scarf", "sprints", 5, "Finish 5 focus sprints"), ("bowtie", "Bowtie", "level", 4, "Reach level 4"),
             ("bell", "Bell", "level", 2, "Reach level 2"), ("bandana", "Bandana", "tasks", 25, "Complete 25 tasks"),
             ("medal", "Medal", "tasks", 50, "Complete 50 tasks")),
    "back": (("cape", "Task cape", "tasks", 25, "Complete 25 tasks"), ("wings", "Wings", "streak", 30, "Keep a 30-day streak"),
             ("pack", "Backpack", "tasks", 40, "Complete 40 tasks"), ("jet", "Jetpack", "sprints", 20, "Finish 20 focus sprints")),
    "hand": (("balloon", "Balloon", "level", 3, "Reach level 3"), ("book", "Book", "tasks", 30, "Complete 30 tasks"),
             ("coffee", "Coffee", "habits", 15, "Keep habits 15 times"), ("flag", "Flag", "tasks", 60, "Complete 60 tasks"),
             ("wand", "Wand", "tasks", 150, "Complete 150 tasks"), ("trophy", "Trophy", "streak", 14, "Keep a 14-day streak")),
    "aura": (("sparkles", "Sparkles", "level", 3, "Reach level 3"), ("bubbles", "Bubbles", "level", 4, "Reach level 4"),
             ("orbit", "Orbit", "level", 5, "Reach level 5"), ("embers", "Embers", "sprints", 8, "Finish 8 focus sprints"),
             ("hearts", "Hearts", "habits", 20, "Keep habits 20 times"), ("notes", "Notes", "tasks", 20, "Complete 20 tasks")),
}
FINISHES = (("asis", "As drawn", "level", 1, "Always"), ("ink", "Ink", "level", 2, "Reach level 2"),
            ("neon", "Neon edge", "level", 3, "Reach level 3"), ("pastel", "Pastel", "habits", 20, "Keep habits 20 times"),
            ("holo", "Holographic", "streak", 14, "Keep a 14-day streak"), ("gold", "Gold", "level", 9, "Reach level 9"))
# The notch's own decoration (shell/ui/js/buddy/notch.js): part -> (id, name, progress key, amount needed, how it is earned).
# The first of each is the default and is always open. Pins are several at once (at most PIN_MAX); the rest are one choice each.
NOTCH_PARTS: dict[str, tuple[tuple[str, str, str, int, str], ...]] = {
    "plate": (("black", "Black", "level", 1, "Always"), ("glass", "Glass", "level", 3, "Reach level 3"),
              ("dots", "Dot matrix", "habits", 10, "Keep habits 10 times"), ("aurora", "Aurora", "level", 6, "Reach level 6")),
    "frame": (("hairline", "Hairline", "level", 1, "Always"), ("glow", "Glow rim", "level", 2, "Reach level 2"),
              ("double", "Double line", "level", 4, "Reach level 4"), ("dashed", "Dashed", "tasks", 20, "Complete 20 tasks"),
              ("rainbow", "Rainbow", "streak", 14, "Keep a 14-day streak")),
    "shape": (("round", "Floating", "level", 1, "Always"), ("attached", "Attached with flares", "level", 3, "Reach level 3")),
    "ledge": (("plain", "Plain edge", "level", 1, "Always"), ("neon", "Neon strip", "level", 3, "Reach level 3"),
              ("rainbow", "Rainbow rim", "streak", 7, "Keep a 7-day streak"), ("wood", "Wood shelf", "level", 5, "Reach level 5"),
              ("gold", "Gold bar", "level", 9, "Reach level 9")),
    "lamp": (("dot", "Dot", "level", 1, "Always"), ("ring", "Ring", "level", 2, "Reach level 2"),
             ("bars", "Bars", "tasks", 10, "Complete 10 tasks"), ("heart", "Heart", "habits", 7, "Keep habits 7 times")),
    "pins": (("star", "Star", "level", 2, "Reach level 2"), ("bolt", "Bolt", "sprints", 3, "Finish 3 focus sprints"),
             ("leaf", "Leaf", "habits", 10, "Keep habits 10 times"), ("flame", "Flame", "streak", 7, "Keep a 7-day streak"),
             ("note", "Note", "tasks", 15, "Complete 15 tasks")),
}
PIN_MAX = 3
# The app window's own decoration (shell/ui/css/decor.css): same rules as the notch's. The first of each is the default.
APP_PARTS: dict[str, tuple[tuple[str, str, str, int, str], ...]] = {
    "skin": (("nocturne", "Nocturne", "level", 1, "Always"), ("paper", "Paper", "level", 2, "Reach level 2"),
             ("aurora", "Aurora glass", "level", 5, "Reach level 5"), ("matrix", "Dot matrix", "habits", 10, "Keep habits 10 times")),
    "backdrop": (("none", "Clean", "level", 1, "Always"), ("dots", "Dots", "level", 2, "Reach level 2"),
                 ("grain", "Scanlines", "tasks", 15, "Complete 15 tasks"), ("glow", "Corner glow", "level", 4, "Reach level 4")),
    "cards": (("hairline", "Hairline", "level", 1, "Always"), ("fill", "Soft fill", "level", 3, "Reach level 3"),
              ("glow", "Accent glow", "streak", 10, "Keep a 10-day streak")),
    "accent": (("buddy", "Its colour", "level", 1, "Always"), ("honey", "Honey", "level", 2, "Reach level 2"),
               ("mint", "Mint", "level", 2, "Reach level 2"), ("sky", "Sky", "level", 3, "Reach level 3"),
               ("rose", "Rose", "habits", 10, "Keep habits 10 times"), ("violet", "Violet", "streak", 14, "Keep a 14-day streak"),
               ("ember", "Ember", "sprints", 10, "Finish 10 focus sprints")),
    "scene": (("plate", "Plain", "level", 1, "Always"), ("skyline", "Skyline", "level", 4, "Reach level 4"),
              ("stars", "Stars", "habits", 15, "Keep habits 15 times")),
}
def _need(key: str, n: int) -> str:
    return {"level": f"Reach level {n}", "tasks": f"Complete {n} tasks", "habits": f"Keep habits {n} times",
            "sprints": f"Finish {n} focus sprints", "streak": f"Keep a {n}-day streak"}[key]


def _table(spec: dict) -> dict:
    """{slot: [(id, name, progress key, amount)]} to the catalog rows with their "how it is earned" text."""
    return {slot: tuple((i, name, key, n, _need(key, n)) for i, name, key, n in rows) for slot, rows in spec.items()}


# The four other characters' items: slot -> (id, name, progress key, amount needed). The ids are the drawn ones in wardrobe-pixel.js.
CHAR_ITEMS: dict[str, dict] = {
    "void": _table({
        "crown": [("accretion", "Accretion ring", "level", 3), ("comet", "Comet tail", "tasks", 30), ("dish", "Signal dish", "habits", 10)],
        "eyes": [("lens", "Gravity lens", "level", 4), ("starry", "Star eyes", "streak", 5)],
        "neck": [("orbitring", "Orbit collar", "level", 2), ("pendant", "Satellite pendant", "tasks", 50)],
        "back": [("nebula", "Nebula cloud", "streak", 14), ("solar", "Solar wings", "tasks", 60)],
        "hand": [("planet", "Ringed planet", "level", 5), ("scope", "Telescope", "tasks", 100)],
        "aura": [("constellation", "Constellation", "streak", 7), ("moons", "Moons", "level", 6), ("meteors", "Meteors", "sprints", 10)]}),
    "glitch": _table({
        "crown": [("bars", "Signal bars", "level", 2), ("pixelcrown", "Pixel crown", "tasks", 40), ("headset", "Headset", "sprints", 5)],
        "eyes": [("pixshades", "Pixel shades", "level", 5), ("xeyes", "Error eyes", "tasks", 20), ("visor", "Scan visor", "sprints", 8)],
        "neck": [("cable", "Cable", "level", 3), ("qr", "QR tag", "tasks", 60)],
        "back": [("wifi", "Wifi waves", "streak", 10), ("circuit", "Circuit cape", "tasks", 80)],
        "hand": [("cursor", "Cursor", "level", 2), ("usb", "USB stick", "habits", 15), ("floppy", "Floppy", "tasks", 120)],
        "aura": [("bitrain", "Bit rain", "level", 4), ("static", "Static", "streak", 7)]}),
    "cinder": _table({
        "crown": [("flamecrown", "Flame crown", "streak", 7), ("emberhalo", "Ember halo", "level", 5), ("chimney", "Smoke stack", "tasks", 25)],
        "eyes": [("goggles", "Welding goggles", "sprints", 6), ("soot", "Soot marks", "level", 2)],
        "neck": [("ashscarf", "Ash scarf", "sprints", 5), ("emberchain", "Ember chain", "tasks", 50)],
        "back": [("phoenix", "Phoenix wings", "streak", 30), ("smoke", "Smoke trail", "level", 4)],
        "hand": [("marsh", "Marshmallow stick", "habits", 10), ("torch", "Torch", "tasks", 70), ("kettle", "Kettle", "habits", 20)],
        "aura": [("sparks", "Sparks", "level", 3), ("heat", "Heat waves", "sprints", 12)]}),
    "oracle": _table({
        "crown": [("thirdeye", "Third eye", "level", 6), ("gemband", "Gem band", "tasks", 50), ("laurel", "Laurel", "streak", 14)],
        "eyes": [("lashes", "Lashes", "level", 2), ("lids", "Heavy lids", "habits", 10), ("runeye", "Rune marks", "tasks", 30)],
        "neck": [("amulet", "Amulet", "level", 4), ("runechain", "Rune chain", "tasks", 40)],
        "back": [("cloak", "Hooded cloak", "tasks", 60), ("rings", "Orrery rings", "streak", 20)],
        "hand": [("ball", "Crystal ball", "level", 5), ("scroll", "Scroll", "tasks", 35), ("candle", "Candle", "habits", 12)],
        "aura": [("runes", "Floating runes", "level", 3), ("eyesfloat", "Little eyes", "streak", 10)]}),
}


def items_for(character: str) -> dict:
    """The catalog of one character: slot -> rows."""
    return ITEMS if character == CHARACTER else CHAR_ITEMS[character]

INKS = ("char", "white", "honey", "mint", "rose", "sky", "violet", "ember")
STYLES = ("line", "pixel")                   # pixel is the default: the audited, shaded drawings
# every item has a pixel-art drawing (shell/ui/js/buddy/wardrobe-pixel.js; a test keeps the two in step). Only Blink's also have line drawings.
PIXEL_ITEMS = frozenset(f"{slot}-{row[0]}" for cat in (ITEMS, *CHAR_ITEMS.values()) for slot, rows in cat.items() for row in rows)

_lock = threading.RLock()
_mem: dict = {}
# Trying a locked decoration on. It is held in memory only (never in the wardrobe file, so it cannot unlock anything), it covers the
# live feed for PREVIEW_S seconds and then goes by itself, and wear / set_notch / set_app still refuse what is not earned.
PREVIEW_S = 90
_preview: dict = {"until": 0.0, "serial": 0, "character": "", "worn": {}, "notch": {}, "pins": [], "app": {}}
_cache: dict = {}                            # per character: the live feed asks many times a second; progress changes rarely
CACHE_S = 2.0


def _path() -> Path | None:
    override = os.environ.get("MBUDDY_WARDROBE_PATH")
    if override:
        return Path(override)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path.home() / ".buddy" / "wardrobe.json"


def current_character() -> str:
    """The buddy the user chose (Settings, Buddy): its wardrobe is the one the live feed carries."""
    try:
        from core import ui_settings
        c = ui_settings.get("character")
    except Exception:
        c = CHARACTER
    return c if c in CHARACTERS else CHARACTER


def _blank_char() -> dict:
    return {"worn": {s: "" for s in SLOTS}, "colors": {s: "" for s in SLOTS}, "styles": {s: "pixel" for s in SLOTS}}


def _blank() -> dict:
    return {"chars": {c: _blank_char() for c in CHARACTERS}, "finish": "asis", "ink": "char", "rev": 0,
            "notch": _blank_notch(), "app": _blank_app()}


def _blank_app() -> dict:
    return {part: rows[0][0] for part, rows in APP_PARTS.items()}


def _blank_notch() -> dict:
    return {**{part: NOTCH_PARTS[part][0][0] for part in NOTCH_PARTS if part != "pins"}, "pins": []}


def _read_char(raw: dict) -> dict:
    out = _blank_char()
    worn = raw.get("worn") if isinstance(raw.get("worn"), dict) else {}
    out["worn"] = {s: str(worn.get(s) or "") for s in SLOTS}
    colors = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
    out["colors"] = {s: (colors.get(s) if colors.get(s) in INKS else "") for s in SLOTS}
    styles = raw.get("styles") if isinstance(raw.get("styles"), dict) else {}
    out["styles"] = {s: (styles.get(s) if styles.get(s) in STYLES else "pixel") for s in SLOTS}
    return out


def _load() -> dict:
    p = _path()
    raw = _mem if p is None else {}
    if p is not None:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
    d = _blank()
    if isinstance(raw, dict):
        chars = raw.get("chars") if isinstance(raw.get("chars"), dict) else None
        if chars is None:
            chars = {CHARACTER: raw}                 # a wardrobe saved before each buddy had its own was Blink's
        for c in CHARACTERS:
            if isinstance(chars.get(c), dict):
                d["chars"][c] = _read_char(chars[c])
        d["finish"] = raw.get("finish") if isinstance(raw.get("finish"), str) else "asis"
        d["ink"] = raw.get("ink") if raw.get("ink") in INKS else "char"
        d["rev"] = int(raw.get("rev") or 0)
        n = raw.get("notch") if isinstance(raw.get("notch"), dict) else {}
        for part in NOTCH_PARTS:
            if part == "pins":
                pins = [x for x in (n.get("pins") or []) if isinstance(x, str) and _row(NOTCH_PARTS["pins"], x)]
                d["notch"]["pins"] = list(dict.fromkeys(pins))[:PIN_MAX]
            elif _row(NOTCH_PARTS[part], n.get(part, "")):
                d["notch"][part] = n[part]
        a = raw.get("app") if isinstance(raw.get("app"), dict) else {}
        for part in APP_PARTS:
            if _row(APP_PARTS[part], a.get(part, "")):
                d["app"][part] = a[part]
    return d


def _save(d: dict) -> None:
    _cache.clear()
    p = _path()
    if p is None:
        _mem.clear()
        _mem.update(d)
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def progress() -> dict:
    """The five numbers the unlock rules read, from what really happened."""
    from core import rewards
    snap = rewards.snapshot()
    t = snap.get("totals") or {}
    return {"level": int(snap["level"]), "tasks": int(t.get("task_done", 0)), "sprints": int(t.get("focus_sprint", 0)),
            "habits": int(t.get("habit", 0)), "streak": int(snap.get("best_streak", 0))}


def _row(table, item_id: str):
    return next((r for r in table if r[0] == item_id), None)


def _open(row, prog: dict) -> bool:
    return prog.get(row[2], 0) >= row[3]


def _notch_effective(d: dict, prog: dict) -> dict:
    """What the notch really wears: a part that is not (or no longer) unlocked falls back to its default."""
    out = {}
    for part, rows in NOTCH_PARTS.items():
        if part == "pins":
            out["pins"] = [x for x in d["notch"]["pins"] if _open(_row(rows, x), prog)][:PIN_MAX]
        else:
            row = _row(rows, d["notch"][part])
            out[part] = row[0] if row and _open(row, prog) else rows[0][0]
    return out


def _app_effective(d: dict, prog: dict) -> dict:
    out = {}
    for part, rows in APP_PARTS.items():
        row = _row(rows, d["app"][part])
        out[part] = row[0] if row and _open(row, prog) else rows[0][0]
    return out


def _effective(d: dict, prog: dict, character: str) -> dict:
    cat, mine = items_for(character), d["chars"][character]
    worn = {}
    for s in SLOTS:
        row = _row(cat.get(s, ()), mine["worn"].get(s, ""))
        worn[s] = row[0] if row and _open(row, prog) else ""
    fin = _row(FINISHES, d["finish"])
    return {"worn": worn, "finish": fin[0] if fin and _open(fin, prog) else "asis"}


def _styles(d: dict, worn: dict, character: str) -> dict:
    """The style actually drawn: pixel only where the worn item has a pixel drawing."""
    mine = d["chars"][character]
    # only Blink's items also exist as line drawings: the others are pixel art, always
    return {s: ("pixel" if f"{s}-{worn.get(s, '')}" in PIXEL_ITEMS and (character != CHARACTER or mine["styles"].get(s) == "pixel") else "line") for s in SLOTS}


def state(prog: dict | None = None, character: str | None = None) -> dict:
    """Everything the settings page shows: each slot's items with whether they are unlocked, and what is worn."""
    prog = prog or progress()
    character = character if character in CHARACTERS else current_character()
    with _lock:
        d = _load()
    cat = items_for(character)
    slots = {s: [{"id": r[0], "name": r[1], "need": r[4], "unlocked": _open(r, prog), "pixel": f"{s}-{r[0]}" in PIXEL_ITEMS,
                  "line": character == CHARACTER}
                 for r in cat.get(s, ())] for s in SLOTS}
    worn = _effective(d, prog, character)
    mine = d["chars"][character]
    return {"character": character, "slots": slots, "worn": worn["worn"], "finish": worn["finish"], "ink": d["ink"],
            "colors": mine["colors"], "styles": _styles(d, worn["worn"], character),
            "finishes": [{"id": r[0], "name": r[1], "need": r[4], "unlocked": _open(r, prog)} for r in FINISHES],
            "inks": list(INKS), "progress": prog, "rev": d["rev"],
            "notch": _notch_effective(d, prog), "pin_max": PIN_MAX, "app": _app_effective(d, prog),
            "app_parts": {part: [{"id": r[0], "name": r[1], "need": r[4], "unlocked": _open(r, prog)} for r in rows]
                          for part, rows in APP_PARTS.items()},
            "notch_parts": {part: [{"id": r[0], "name": r[1], "need": r[4], "unlocked": _open(r, prog)} for r in rows]
                            for part, rows in NOTCH_PARTS.items()},
            "preview": _preview_state(character)}


def _preview_state(character: str) -> dict:
    """What is being tried on right now, so Settings can mark it and show the countdown."""
    if not _preview_on():
        return {"active": False, "left": 0, "worn": {}, "notch": {}, "pins": [], "app": {}, "items": []}
    return {"active": True, "left": max(0, int(_preview["until"] - time.time())), "worn": dict(_preview["worn"]) if _preview["character"] == character else {},
            "notch": dict(_preview["notch"]), "pins": list(_preview["pins"]), "app": dict(_preview["app"]), "items": _names_previewed()}


def live(prog: dict | None = None, character: str | None = None) -> dict:
    """The small part the live feed carries so a window paints what the chosen buddy wears (never an item that is not unlocked)."""
    character = character if character in CHARACTERS else current_character()
    trying = _preview_on()
    now = time.monotonic()
    hit = _cache.get(character)
    if prog is None and hit is not None and now - hit[0] < CACHE_S and not trying:
        return hit[1]
    p = prog or progress()
    with _lock:
        d = _load()
    e = _effective(d, p, character)
    out = {"character": character, "worn": e["worn"], "finish": e["finish"], "ink": d["ink"], "rev": d["rev"],
           "colors": d["chars"][character]["colors"], "styles": _styles(d, e["worn"], character), "notch": _notch_effective(d, p),
           "app": _app_effective(d, p)}
    if trying:
        _overlay_preview(out, d, character)
    if prog is None and not trying:
        _cache[character] = (now, out)
    return out


def _preview_on() -> bool:
    """Is a preview running? One that ran out is cleared here (and the change counter moves, so windows repaint)."""
    with _lock:
        if _preview["until"] and time.time() >= _preview["until"]:
            _drop_preview()
        return bool(_preview["until"])


def _drop_preview() -> None:
    _preview.update({"until": 0.0, "character": "", "worn": {}, "notch": {}, "pins": [], "app": {}})
    _preview["serial"] += 1
    _cache.clear()


def _overlay_preview(out: dict, d: dict, character: str) -> None:
    """Lay what is being tried on over what is really worn. The pixel/line style follows the drawing that exists."""
    if _preview["character"] == character:
        for slot, item in _preview["worn"].items():
            out["worn"][slot] = item
            out["styles"][slot] = "pixel" if f"{slot}-{item}" in PIXEL_ITEMS and (character != CHARACTER or d["chars"][character]["styles"].get(slot) == "pixel") else "line"
    out["notch"] = {**out["notch"], **_preview["notch"]}
    if _preview["pins"]:
        out["notch"]["pins"] = list(dict.fromkeys([*out["notch"]["pins"], *_preview["pins"]]))[-PIN_MAX:]
    out["app"] = {**out["app"], **_preview["app"]}
    out["rev"] = d["rev"] * 10000 + _preview["serial"] % 10000
    out["preview"] = {"left": max(0, int(_preview["until"] - time.time())), "serial": _preview["serial"]}


def _names_previewed() -> list[dict]:
    """What is being tried on, with what it takes to earn it (for the banner)."""
    out = []
    ch = _preview["character"]
    for slot, item in _preview["worn"].items():
        row = _row(items_for(ch).get(slot, ()), item) if ch else None
        if row:
            out.append({"what": f"{row[1]} ({slot})", "need": row[4]})
    for part, value in _preview["notch"].items():
        row = _row(NOTCH_PARTS[part], value)
        out.append({"what": f"{row[1]} (notch {part})", "need": row[4]})
    for pin in _preview["pins"]:
        row = _row(NOTCH_PARTS["pins"], pin)
        out.append({"what": f"{row[1]} (notch pin)", "need": row[4]})
    for part, value in _preview["app"].items():
        row = _row(APP_PARTS[part], value)
        out.append({"what": f"{row[1]} (app {part})", "need": row[4]})
    return out


def preview(kind: str, part: str, value: str, character: str | None = None) -> tuple[bool, str]:
    """Try something on for PREVIEW_S seconds without earning it: kind "wear" (part is the slot), "notch" or "app" (part is the part).
    Nothing is saved and nothing is unlocked."""
    if kind == "wear":
        if part not in SLOTS:
            return False, "That is not a place the character wears things."
        character = _char(character)
        if _row(items_for(character).get(part, ()), value) is None:
            return False, "I do not know that item."
    elif kind == "notch":
        if part not in NOTCH_PARTS or _row(NOTCH_PARTS[part], value) is None:
            return False, "I do not know that one."
    elif kind == "app":
        if part not in APP_PARTS or _row(APP_PARTS[part], value) is None:
            return False, "I do not know that one."
    else:
        return False, "I cannot try that on."
    _preview_on()
    with _lock:
        if kind == "wear":
            if _preview["character"] != character:
                _preview["worn"] = {}
            _preview["character"] = character
            _preview["worn"][part] = value
        elif kind == "notch" and part == "pins":
            _preview["pins"] = [*[p for p in _preview["pins"] if p != value], value][-PIN_MAX:]
        elif kind == "notch":
            _preview["notch"][part] = value
        else:
            _preview["app"][part] = value
        _preview["until"] = time.time() + PREVIEW_S
        _preview["serial"] += 1
        _cache.clear()
    return True, ""


def stop_preview() -> None:
    with _lock:
        if _preview["until"]:
            _drop_preview()


def _char(character: str | None) -> str:
    return character if character in CHARACTERS else current_character()


def wear(slot: str, item_id: str, prog: dict | None = None, character: str | None = None) -> tuple[bool, str]:
    """Put an item on ("" takes the slot's item off). Refuses what is unknown or not yet earned, with the reason."""
    if slot not in SLOTS:
        return False, "That is not a place the character wears things."
    character = _char(character)
    prog = prog or progress()
    if item_id:
        row = _row(items_for(character).get(slot, ()), item_id)
        if row is None:
            return False, "I do not know that item."
        if not _open(row, prog):
            return False, f"Not yet: {row[4]}."
    with _lock:
        d = _load()
        d["chars"][character]["worn"][slot] = item_id
        d["rev"] += 1
        _save(d)
    return True, ""


def set_finish(finish_id: str, prog: dict | None = None) -> tuple[bool, str]:
    prog = prog or progress()
    row = _row(FINISHES, finish_id)
    if row is None:
        return False, "I do not know that look."
    if not _open(row, prog):
        return False, f"Not yet: {row[4]}."
    with _lock:
        d = _load()
        d["finish"] = finish_id
        d["rev"] += 1
        _save(d)
    return True, ""


def set_ink(ink: str) -> tuple[bool, str]:
    if ink not in INKS:
        return False, "I do not know that ink."
    with _lock:
        d = _load()
        d["ink"] = ink
        d["rev"] += 1
        _save(d)
    return True, ""


def set_color(slot: str, ink: str, character: str | None = None) -> tuple[bool, str]:
    """One item's own colour. "" makes it follow the general ink again."""
    if slot not in SLOTS:
        return False, "That is not a place the character wears things."
    if ink and ink not in INKS:
        return False, "I do not know that colour."
    character = _char(character)
    with _lock:
        d = _load()
        d["chars"][character]["colors"][slot] = ink
        d["rev"] += 1
        _save(d)
    return True, ""


def set_style(slot: str, style: str, character: str | None = None) -> tuple[bool, str]:
    """Draw a slot's item as a line drawing or as pixel art (only items with a pixel drawing can be pixel)."""
    if slot not in SLOTS:
        return False, "That is not a place the character wears things."
    if style not in STYLES:
        return False, "I do not know that style."
    character = _char(character)
    with _lock:
        d = _load()
        item = d["chars"][character]["worn"].get(slot, "")
        if style == "pixel" and f"{slot}-{item}" not in PIXEL_ITEMS:
            return False, "That item has no pixel drawing yet."
        if style == "line" and character != CHARACTER:
            return False, "This buddy's decorations are pixel art only."
        d["chars"][character]["styles"][slot] = style
        d["rev"] += 1
        _save(d)
    return True, ""


def set_notch(part: str, value: str, prog: dict | None = None) -> tuple[bool, str]:
    """Choose one part of the notch's decoration. For "pins", value toggles that pin on or off (at most PIN_MAX)."""
    if part not in NOTCH_PARTS:
        return False, "That is not a part of the notch I can decorate."
    row = _row(NOTCH_PARTS[part], value)
    if row is None:
        return False, "I do not know that one."
    prog = prog or progress()
    if not _open(row, prog):
        return False, f"Not yet: {row[4]}."
    with _lock:
        d = _load()
        if part == "pins":
            pins = d["notch"]["pins"]
            if value in pins:
                pins.remove(value)
            elif len(pins) >= PIN_MAX:
                return False, f"The notch holds {PIN_MAX} pins. Take one off first."
            else:
                pins.append(value)
        else:
            d["notch"][part] = value
        d["rev"] += 1
        _save(d)
    return True, ""


def set_app(part: str, value: str, prog: dict | None = None) -> tuple[bool, str]:
    """Choose one part of the app window's decoration (skin, backdrop, cards, accent, scene)."""
    if part not in APP_PARTS:
        return False, "That is not a part of the app I can decorate."
    row = _row(APP_PARTS[part], value)
    if row is None:
        return False, "I do not know that one."
    prog = prog or progress()
    if not _open(row, prog):
        return False, f"Not yet: {row[4]}."
    with _lock:
        d = _load()
        d["app"][part] = value
        d["rev"] += 1
        _save(d)
    return True, ""


def take_everything_off(character: str | None = None) -> None:
    """Take off what the chosen buddy wears (its colours and styles too), and reset the look and the notch and app decoration."""
    character = _char(character)
    stop_preview()
    with _lock:
        d = _load()
        d["chars"][character] = _blank_char()
        d["finish"] = "asis"
        d["notch"] = _blank_notch()
        d["app"] = _blank_app()
        d["rev"] += 1
        _save(d)


def reset() -> None:
    """Tests only."""
    with _lock:
        _mem.clear()
        _cache.clear()
        _preview.update({"until": 0.0, "serial": 0, "character": "", "worn": {}, "notch": {}, "pins": [], "app": {}})
