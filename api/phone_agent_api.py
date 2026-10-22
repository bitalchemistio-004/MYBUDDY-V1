"""api/phone_agent_api.py - the brain's side of "do this on my phone".

The phone app reads what is on its screen (the accessibility tree, as a short numbered list), says what the user wants, and asks
for ONE next step. This answers with one action, as JSON. The phone does the action, looks again, and asks again, until the
task is done. The phone, not this file, decides which steps need the user's yes first (send, pay, delete, post, call): whatever
this returns, those are held back there. This file only chooses steps and checks that what it returns is well formed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import requests
from aiohttp import web

import utils.logger as log
from api.device_control import _require_local
from core import cloud

ACTIONS = ("tap", "type", "scroll", "back", "home", "open", "open_url", "wait", "ask", "done", "fail")
URL_SCHEMES = ("https", "tel", "smsto", "sms", "mailto", "geo")
MAX_SCREEN = 9000
MAX_GOAL = 600

SYSTEM = """You operate an Android phone for its owner, through its accessibility tree, to finish one task.
Each turn you get the task, the steps already taken, and the current screen as a numbered list. Answer with ONE JSON object, nothing else.

Actions:
{"do":"open","app":"<app name as shown on the phone>"}      open an app
{"do":"open_url","url":"https://... | tel:... | smsto:+961...?body=... | geo:..."}   open a link (a prefilled message or call screen)
{"do":"tap","id":N}                                          tap element N from the list
{"do":"type","id":N,"text":"..."}                            put text in the field N (replaces what is there)
{"do":"scroll","dir":"down"|"up"}                            scroll the main list
{"do":"back"}   {"do":"home"}   {"do":"wait"}                 go back, go home, give the screen a moment to load
{"do":"ask","say":"...","options":["Yes","No"]}              ask the owner a short question. Give "options" (2 to 4 short choices) ONLY when the answer is one of them
                                                             (a yes/no question, or a pick from a list). For an open question (a name, a colour, a number)
                                                             leave "options" out: the owner then types or says the answer.
{"do":"done","say":"..."}                                    the task is finished; say the result in one short sentence
{"do":"fail","say":"..."}                                    it cannot be done; say why in one short sentence
Every action may carry "say": one short sentence, spoken to the owner, about what you are doing now. Keep it under 12 words.

Rules:
- FIRST look at the current screen and the steps so far. Start from where things already are: if the right app is already open, do not open it again; if the right page or chat is already open (the person's name is at the top of the screen), skip straight to the step that is left; if the text is already typed, do not type it again.
- Use only elements that are in the list. Never invent a name, number or address; if it is not on screen or in the task, ask.
- To message someone, open the chat, type the message in the message field, then tap Send. The phone will stop and read the message
  back to the owner before anything is sent, so do not ask for confirmation yourself.
- Prefer the shortest path: a link (open_url) over many taps when the task gives you the number or address.
- Never type passwords, codes, card numbers or anything secret. If the screen asks for them, use "ask" or "fail".
- If you have repeated the same step twice without the screen changing, try something different, or "fail".
- When the task is complete, answer "done" with what you found or did. Do not keep going."""


LOCAL_URL = os.getenv("MBUDDY_LOCAL_OLLAMA", "http://127.0.0.1:11434")
LOCAL_PICK = Path.home() / ".buddy" / "phone_agent_brain.txt"      # "local" or "local:<model>" to plan with the PC's own model; absent = the cloud brain


def chosen_brain(requested: str = "") -> tuple[str, str]:
    """(where, model): where is "cloud" or "local". What the phone asked for wins, then the file, then the cloud."""
    want = (requested or "").strip()
    if not want:
        try:
            want = LOCAL_PICK.read_text(encoding="utf-8").strip()
        except OSError:
            want = ""
    if want.startswith("local"):
        return "local", want.partition(":")[2].strip()
    return "cloud", ""


def local_chat(messages: list[dict], model: str = "", max_tokens: int = 700, timeout: float = 120.0) -> tuple[str, str]:
    """One step from the PC's own Ollama (no cloud). Returns (text, model). Raises RuntimeError with a plain reason."""
    try:
        if not model:
            tags = requests.get(LOCAL_URL + "/api/tags", timeout=4).json().get("models", [])
            names = [m.get("name", "") for m in tags if "embed" not in m.get("name", "")]
            if not names:
                raise RuntimeError("No local model is installed on the PC.")
            model = names[0]
        r = requests.post(LOCAL_URL + "/api/chat", timeout=timeout, json={
            "model": model, "messages": messages, "stream": False, "think": False, "format": "json", "keep_alive": "5m",
            "options": {"temperature": 0.1, "num_predict": max_tokens}})
        if r.status_code != 200:
            raise RuntimeError(f"The local model answered with an error ({r.status_code}).")
        return str((r.json().get("message") or {}).get("content", "")).strip(), model
    except requests.RequestException as exc:
        raise RuntimeError("The local model isn't running on the PC.") from exc


def _clip(text, n: int) -> str:
    return str(text or "")[:n]


def build_messages(goal: str, screen: str, history: list, app: str = "") -> list[dict]:
    steps = []
    for h in (history or [])[-8:]:
        if isinstance(h, dict):
            steps.append(f"- {_clip(h.get('did'), 160)}" + (f" -> {_clip(h.get('result'), 80)}" if h.get("result") else ""))
    user = (f"Task: {_clip(goal, MAX_GOAL)}\n"
            f"Steps so far:\n{chr(10).join(steps) if steps else '- none yet'}\n"
            f"Current app: {_clip(app, 80) or 'unknown'}\n"
            f"Screen:\n{_clip(screen, MAX_SCREEN)}\n"
            "Next step (one JSON object):")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse_action(text: str) -> dict | None:
    """The first JSON object in the model's words, checked and trimmed. None when it is not a usable step."""
    raw = str(text or "")
    start = raw.find("{")
    if start < 0:
        return None
    depth, end = 0, -1
    in_str, esc = False, False
    for i in range(start, len(raw)):
        c = raw[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("do", "")).strip().lower()
    if kind not in ACTIONS:
        return None
    out: dict = {"do": kind}
    say = _clip(data.get("say"), 240).strip()
    if say:
        out["say"] = say
    if kind in ("tap", "type"):
        try:
            out["id"] = int(data["id"])
        except (KeyError, TypeError, ValueError):
            return None
        if out["id"] < 0:
            return None
    if kind == "type":
        out["text"] = _clip(data.get("text"), 600)
    if kind == "scroll":
        out["dir"] = "up" if str(data.get("dir", "")).lower() == "up" else "down"
    if kind == "open":
        app = _clip(data.get("app"), 60).strip()
        if not app:
            return None
        out["app"] = app
    if kind == "open_url":
        url = _clip(data.get("url"), 1200).strip()
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme not in URL_SCHEMES:
            return None
        out["url"] = url
    if kind == "ask" and isinstance(data.get("options"), list):
        options = [_clip(o, 40).strip() for o in data["options"] if str(o).strip()][:4]
        if options:
            out["options"] = options
    if kind in ("ask", "done", "fail") and "say" not in out:
        out["say"] = {"ask": "I need to know something.", "done": "Done.", "fail": "I couldn't do that."}[kind]
    return out


async def step_handler(request: web.Request) -> web.Response:
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Send a JSON object."}, status=400)
    goal = _clip((data or {}).get("goal"), MAX_GOAL).strip()
    if not goal:
        return web.json_response({"ok": False, "error": "No task given."}, status=400)
    messages = build_messages(goal, (data or {}).get("screen", ""), (data or {}).get("history", []), (data or {}).get("app", ""))
    where, local_model = chosen_brain(str((data or {}).get("brain", "")))
    if where == "local":
        try:
            text, used = await asyncio.to_thread(local_chat, messages, local_model)
        except RuntimeError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=503)
        action = parse_action(text)
        if action is None:
            log.warn(f"[PhoneAgent] local model gave an unusable step: {text[:120]!r}")
            return web.json_response({"ok": False, "error": "The local model didn't give a usable step.", "retry": True}, status=502)
        return web.json_response({"ok": True, "action": action, "model": used + " (on the PC)"})
    why = cloud.check_usable("main")
    if why:
        return web.json_response({"ok": False, "error": why}, status=503)
    reply = None
    for attempt in range(2):        # a model now and then answers with nothing or a busy signal: once more is cheap, giving up is not
        try:
            reply = await asyncio.to_thread(cloud.chat, messages, kind="main", purpose="phone_agent", max_tokens=1500, timeout=60.0)
            break
        except cloud.CloudError as exc:
            if attempt == 0 and exc.kind in ("bad", "rate", "offline"):
                await asyncio.sleep(1.0)
                continue
            return web.json_response({"ok": False, "error": str(exc)}, status=503)
    action = parse_action(reply.text) if reply else None
    if action is None:
        log.warn(f"[PhoneAgent] unusable step: {reply.text[:120]!r}")
        return web.json_response({"ok": False, "error": "The brain didn't give a usable step.", "retry": True}, status=502)
    return web.json_response({"ok": True, "action": action, "model": reply.model})


def register(app: web.Application) -> None:
    app.router.add_post("/ui/phone-agent/step", step_handler)
