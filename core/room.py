"""room.py - each character's room, and the reward points that build it.

Reward points are the XP the user really earned (core/rewards.py: a task, a focus sprint, a habit) minus what they have spent here,
so spending never lowers the level (the level is computed from the XP, not the balance). Nothing here pays points: games, secrets and
the room itself give none, so there is nothing to farm. What is bought, how far each place is upgraded and where things stand is stored
on this PC (~/.buddy/room.json) and the log of every purchase keeps its time.

The catalog is the contract with the drawing in shell/ui/js/room/: the place and item ids here are the drawn ones there.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

CHARACTERS = ("blink", "void", "glitch", "cinder", "oracle")
PLACES = ("window", "art", "shelf", "lamp", "seat", "rug", "desk", "plant", "sig")
MAX_LEVEL = 5
UPGRADE_COST = (0, 30, 60, 120, 240)          # the cost of reaching level n is UPGRADE_COST[n - 1] (level 1 is free)
# id -> (name, cost, level needed, what it really does)
SHOP = {
    "clock": ("Wall clock", 40, 1, "Shows your real time on the wall."),
    "lights": ("String lights", 60, 2, "Glow brighter on days with more finished tasks."),
    "calendar": ("Wall calendar", 80, 2, "Opens a month view of your real events."),
    "radio": ("Radio", 100, 3, "Mini player controls; the buddy dances when it plays."),
    "tv": ("Game console and TV", 150, 3, "Opens the games. Games never pay points."),
    "tank": ("Fish tank", 120, 4, "One more fish for every five focus sprints you finish."),
    "pet": ("A pet", 200, 5, "Follows the buddy around and naps when you do."),
}
LOG_KEEP = 200
_lock = threading.RLock()
_mem: dict = {}


def _path() -> Path | None:
    override = os.environ.get("MBUDDY_ROOM_PATH")
    if override:
        return Path(override)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path.home() / ".buddy" / "room.json"


def _blank() -> dict:
    return {"levels": {c: {p: 1 for p in PLACES} for c in CHARACTERS}, "owned": {c: {} for c in CHARACTERS},
            "layout": {c: {} for c in CHARACTERS}, "log": [], "best": {}, "trophies": {}, "eggs": {}}


def _load() -> dict:
    p = _path()
    raw = _mem if p is None else {}
    if p is not None:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
    d = _blank()
    if not isinstance(raw, dict):
        return d
    for c in CHARACTERS:
        lv = (raw.get("levels") or {}).get(c) if isinstance(raw.get("levels"), dict) else None
        if isinstance(lv, dict):
            d["levels"][c] = {pl: min(MAX_LEVEL, max(1, int(lv.get(pl) or 1))) for pl in PLACES}
        ow = (raw.get("owned") or {}).get(c) if isinstance(raw.get("owned"), dict) else None
        if isinstance(ow, dict):
            d["owned"][c] = {i: True for i in ow if i in SHOP and ow[i]}
        la = (raw.get("layout") or {}).get(c) if isinstance(raw.get("layout"), dict) else None
        if isinstance(la, dict):
            d["layout"][c] = {k: int(v) for k, v in la.items() if (k in PLACES or k in SHOP) and isinstance(v, (int, float))}
    if isinstance(raw.get("log"), list):
        d["log"] = [e for e in raw["log"] if isinstance(e, dict)][-LOG_KEEP:]
    for k in ("best", "trophies", "eggs"):
        if isinstance(raw.get(k), dict):
            d[k] = raw[k]
    return d


def _save(d: dict) -> None:
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


def _xp_and_level() -> tuple[int, int]:
    from core import rewards
    snap = rewards.snapshot()
    return int(snap["xp"]), int(snap["level"])


def _sprints() -> int:
    from core import rewards
    return int((rewards.snapshot().get("totals") or {}).get("focus_sprint", 0))


def spent(d: dict) -> int:
    total = 0
    for c in CHARACTERS:
        for lv in d["levels"][c].values():
            total += sum(UPGRADE_COST[1:lv])             # every level above 1 was paid for
        total += sum(SHOP[i][1] for i in d["owned"][c])
    return total


def balance(d: dict | None = None, xp: int | None = None) -> int:
    d = d or _load()
    xp = _xp_and_level()[0] if xp is None else xp
    return max(0, xp - spent(d))


def state(xp: int | None = None, level: int | None = None) -> dict:
    """Everything the room page needs: the balance, each character's places and purchases, what is next."""
    if xp is None or level is None:
        xp, level = _xp_and_level()
    with _lock:
        d = _load()
    sp = spent(d)
    return {"points": max(0, xp - sp), "spent": sp, "level": level, "sprints": _sprints(), "levels": d["levels"], "owned": d["owned"], "layout": d["layout"],
            "log": d["log"][-12:], "best": d["best"], "trophies": d["trophies"], "eggs": d["eggs"],
            "upgrade_cost": list(UPGRADE_COST), "max_level": MAX_LEVEL, "places": list(PLACES),
            "shop": [{"id": k, "name": v[0], "cost": v[1], "level": v[2], "real": v[3]} for k, v in SHOP.items()]}


def upgrade(character: str, place: str, xp: int | None = None, level: int | None = None) -> tuple[bool, str]:
    if character not in CHARACTERS or place not in PLACES:
        return False, "I do not know that place."
    if xp is None or level is None:
        xp, level = _xp_and_level()
    with _lock:
        d = _load()
        cur = d["levels"][character][place]
        if cur >= MAX_LEVEL:
            return False, "That place is fully upgraded."
        cost = UPGRADE_COST[cur]
        have = max(0, xp - spent(d))
        if have < cost:
            return False, f"That costs {cost} points and you have {have}."
        d["levels"][character][place] = cur + 1
        d["log"].append({"at": time.time(), "character": character, "what": place, "level": cur + 1, "cost": cost})
        d["log"] = d["log"][-LOG_KEEP:]
        _save(d)
    return True, ""


def buy(character: str, item: str, xp: int | None = None, level: int | None = None) -> tuple[bool, str]:
    if character not in CHARACTERS or item not in SHOP:
        return False, "I do not know that item."
    if xp is None or level is None:
        xp, level = _xp_and_level()
    name, cost, need, _ = SHOP[item]
    with _lock:
        d = _load()
        if d["owned"][character].get(item):
            return False, "That is already in the room."
        if level < need:
            return False, f"Not yet: reach level {need}."
        have = max(0, xp - spent(d))
        if have < cost:
            return False, f"That costs {cost} points and you have {have}."
        d["owned"][character][item] = True
        d["log"].append({"at": time.time(), "character": character, "what": item, "level": 1, "cost": cost, "bought": name})
        d["log"] = d["log"][-LOG_KEEP:]
        _save(d)
    return True, ""


def move(character: str, thing: str, dx: int) -> tuple[bool, str]:
    """Slide one place or bought item sideways (room units, snapped to the 6-unit pixel grid by the caller)."""
    if character not in CHARACTERS or not (thing in PLACES or thing in SHOP):
        return False, "I do not know that."
    if thing in SHOP:
        with _lock:
            if not _load()["owned"][character].get(thing):
                return False, "That is not in the room yet."
    with _lock:
        d = _load()
        d["layout"][character][thing] = max(-900, min(900, int(dx)))
        _save(d)
    return True, ""


def put_back(character: str) -> None:
    with _lock:
        d = _load()
        if character in CHARACTERS:
            d["layout"][character] = {}
            _save(d)


def record_game(game: str, score: int, better_is_lower: bool = False) -> dict:
    """Keep the best score and the trophy of a game. A game never pays points."""
    if game not in ("catch", "pong", "memory"):
        return {}
    with _lock:
        d = _load()
        old = d["best"].get(game)
        if old is None or (score < old if better_is_lower else score > old):
            d["best"][game] = int(score)
        _save(d)
        return {"best": d["best"].get(game)}


def trophy(game: str) -> None:
    if game in ("catch", "pong", "memory"):
        with _lock:
            d = _load()
            d["trophies"][game] = True
            _save(d)


def find_egg(egg: str) -> None:
    if egg in ("poster", "konami", "plant", "star", "disco", "mouse"):
        with _lock:
            d = _load()
            d["eggs"][egg] = True
            _save(d)


def reset() -> None:
    """Tests only."""
    with _lock:
        _mem.clear()
