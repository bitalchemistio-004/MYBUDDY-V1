"""api/learned_api.py - What I learned: the brain, honestly (docs/reports/ARCHITECTURE_MAP).

Everything comes from the user's own vault and from benchmark results on disk;
nothing is made up. A note counts as "checked" only through the numbers the
brain itself keeps (successes and failures from code-checked results, U2), and
"unverified" notes are shown as such. The user can make MBUDDY stop using a
note here (status: archived; nothing is deleted).
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web

from api.device_control import _require_local

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "docs" / "audit" / "bench" / "brain_results.json"

KIND_GROUP = {"Debugging experience": "lessons", "Lessons": "lessons", "Skills": "skills", "Facts": "facts",
              "Preferences": "about you", "People": "about you", "Knowledge": "facts", "Apps": "apps",
              "Episodes": "episodes"}


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _short(body: str, n: int = 240) -> str:
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    text = re.sub(r"[#>*`_\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip()[:n]


def _parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def collect() -> dict:
    from brain import brain_store
    vault = brain_store.get_vault()
    if vault is None:
        return {"linked": False, "items": [], "counts": {}, "week": {}, "score": score()}
    items = []
    for path in vault.notes():
        meta, body, _mtime = vault.read(path)
        if not meta or meta.get("status") == "archived":
            continue
        kind = path.parent.name
        ok, bad = int(meta.get("successes", 0) or 0), int(meta.get("failures", 0) or 0)
        items.append({
            "id": str(path.relative_to(vault.base)).replace("\\", "/"),
            "title": path.stem, "kind": kind, "group": KIND_GROUP.get(kind, "facts"),
            "text": _short(body), "status": meta.get("status") or "candidate",
            "strength": round(float(meta.get("strength", 0) or 0), 2),
            "confidence": round(float(meta.get("confidence", 0) or 0), 2),
            "successes": ok, "failures": bad, "checked": ok + bad,
            "uses": int(meta.get("uses", 0) or 0), "source": meta.get("source") or "mbuddy",
            "last_used": meta.get("last_used"), "created": meta.get("created"),
        })
    items += _taste_items()
    counts: dict[str, int] = {}
    for it in items:
        counts[it["group"]] = counts.get(it["group"], 0) + 1
    since = datetime.now() - timedelta(days=7)
    new = [it for it in items if (_parse(it["created"]) or datetime.min) >= since]
    user = [it for it in new if it["source"] == "user"]
    week = {"new": len(new), "from_user": len(user), "skills": sum(1 for it in items if it["group"] == "skills"
                                                                    and it["status"] != "unverified")}
    items.sort(key=lambda it: (-(it["checked"] > 0), -it["strength"], it["title"]))
    return {"linked": True, "items": items, "counts": counts, "week": week, "score": score()}


def _taste_items() -> list[dict]:
    """What MBUDDY learned of the user's music taste (only when they turned it on): each with its evidence."""
    from core import music_store
    if not music_store.settings()["learn"]:
        return []
    p, out = music_store.profile(), []

    def item(a, text, ok, bad):
        return {"id": "taste:" + a["id"], "title": a["name"] if a["kind"] != "time" else f"Music in the {a['name']}",
                "kind": "Music taste", "group": "about you", "text": text, "status": "known", "strength": 0, "confidence": 0,
                "successes": ok, "failures": bad, "checked": ok + bad, "uses": 0, "source": "your music",
                "last_used": None, "created": None}
    for a in p["liked"]:
        out.append(item(a, f"You finish this: {a['finished']} of {a['listens']} plays, for example {a['top_track']}.", a["finished"], a["listens"] - a["finished"]))
    for a in p["disliked"]:
        out.append(item(a, f"You usually skip this: {a['skipped']} of {a['listens']} plays.", a["listens"] - a["skipped"], a["skipped"]))
    for a in p["times"]:
        out.append(item(a, f"You finish songs in the {a['name']}: {a['finished']} of them.", a["finished"], 0))
    return out


def apps_learned() -> list[dict]:
    """Per connected app: how many skills MBUDDY has for it and how well they are checked."""
    from brain import brain_store
    from core import ui_settings
    vault = brain_store.get_vault()
    apps = []
    for a in ui_settings.connected_apps():
        name = str(a.get("name", ""))
        base = re.sub(r"\s+\d+(\.\d+)*$", "", name).lower()       # "Blender 4.3" -> "blender"
        skills = ok = bad = unverified = 0
        last = None
        if vault is not None:
            for path in vault.notes():
                if path.parent.name not in ("Skills", "Apps") or base not in path.stem.lower():
                    continue
                meta, _body, _m = vault.read(path)
                if not meta or meta.get("status") == "archived":
                    continue
                if path.parent.name == "Skills":
                    skills += 1
                ok += int(meta.get("successes", 0) or 0)
                bad += int(meta.get("failures", 0) or 0)
                unverified += meta.get("status") == "unverified"
                last = max(filter(None, [last, str(meta.get("last_used") or "")]), default=last)
        apps.append({"name": name, "skills": skills, "successes": ok, "failures": bad, "unverified": unverified,
                     "last_used": last, "learned": skills > 0})
    return apps


def score() -> dict | None:
    """The latest A-E benchmark (tools/bench_brain.py), or None when it was never run."""
    try:
        data = json.loads(BENCH.read_text(encoding="utf-8"))
        stamp = datetime.fromtimestamp(BENCH.stat().st_mtime).isoformat(timespec="minutes")
    except (OSError, ValueError):
        return None
    modes = []
    for name, s in (data.get("summary") or {}).items():
        ho = s.get("held_out") or [0, 0]
        modes.append({"mode": name, "passed": ho[0], "of": ho[1], "tokens": s.get("brain_tokens_max", 0),
                      "secs": s.get("secs_per_turn")})
    return {"measured": stamp, "measure": "questions it was not tuned on", "modes": modes} if modes else None


def forget(note_id: str) -> bool:
    """Make MBUDDY stop using one note (status: archived; the file stays)."""
    from brain import brain_store
    from brain.obsidian_vault import join_note
    if note_id.startswith("taste:"):
        from core import music_store
        music_store.forget(note_id[len("taste:"):])
        return True
    vault = brain_store.get_vault()
    if vault is None:
        return False
    path = (vault.base / note_id).resolve()
    try:
        path.relative_to(vault.base.resolve())
    except ValueError:
        return False
    if not path.is_file() or path.suffix != ".md":
        return False
    meta, body, mtime = vault.read(path)
    if meta is None:
        return False
    meta = dict(meta)
    meta["status"] = "archived"
    vault._atomic_write(path, join_note(meta, body), expect_mtime=mtime)
    return True


async def learned_handler(request: web.Request) -> web.Response:
    _require_local(request)
    if request.method == "DELETE":
        try:
            data = await request.json()
        except Exception:
            return _json({"ok": False, "error": "Send a JSON object."}, 400)
        note_id = str((data or {}).get("id") or "")
        if not note_id or ".." in note_id:
            return _json({"ok": False, "error": "id must be a note"}, 400)
        done = await asyncio.to_thread(forget, note_id)
        return _json({"ok": done} if done else {"ok": False, "error": "No such note."}, 200 if done else 404)
    data = await asyncio.to_thread(collect)
    return _json({"ok": True, **data})


async def apps_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json({"ok": True, "apps": await asyncio.to_thread(apps_learned)})


def register(app: web.Application) -> None:
    app.router.add_get("/ui/learned/apps", apps_handler)
    app.router.add_get("/ui/learned", learned_handler)
    app.router.add_delete("/ui/learned", learned_handler)
