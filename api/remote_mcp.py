"""MBUDDY as an MCP server, so a phone's own on-device model can use the PC.

Google AI Edge Gallery (Android, iOS soon) runs Gemma 4 on the phone and can connect to an MCP server by its
URL (MCP over Streamable HTTP). Point it at  https://<this PC's private address>/mcp/<phone token>  and the
model on the phone chooses MBUDDY's tools while the PC carries them out. This is the "working on your phone"
mode that needs no app of ours: the thinking happens on the phone, the doing on the PC.

What it offers is deliberately small and safe: read what MBUDDY is doing, list and add tasks, ask MBUDDY a
question (never spoken on the PC). It cannot approve a PC action: that stays a human's tap.

The transport follows the MCP 2025-06-18 spec for a minimal server: one endpoint, JSON replies, 202 for
notifications, 405 for GET (no server-initiated stream), no session id. Because the Gallery takes only a URL,
the phone's pairing token is in the path; it is the same token the phone page uses, so Pause access and Remove
cut it off like any other phone. A request that carries a browser Origin header is refused (DNS rebinding).
"""
from __future__ import annotations

import asyncio
import json

import aiohttp
from aiohttp import web

import utils.logger as log
from core import remote

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "MBUDDY", "version": "1.0"}

TOOLS = [
    {"name": "mbuddy_status", "description": "What MBUDDY is doing now, running timers and the volume.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mbuddy_tasks", "description": "List the user's tasks and reminders.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mbuddy_add_task", "description": "Add a task or reminder, e.g. 'call the bank' with when 'tomorrow at 9'.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string", "description": "what to remember"},
                                                      "when": {"type": "string", "description": "optional: tomorrow at 9, in 20 minutes"}},
                     "required": ["title"]}},
    {"name": "mbuddy_ask", "description": "Ask MBUDDY on the PC a question or give it a request. It answers in text only.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]


def _rpc_error(id_, code: int, message: str, status: int = 200) -> web.Response:
    return web.json_response({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}, status=status)


def _result(id_, result: dict) -> web.Response:
    return web.json_response({"jsonrpc": "2.0", "id": id_, "result": result}, headers={"Cache-Control": "no-store"})


async def _backend(request: web.Request, method: str, path: str, body: dict | None = None) -> dict:
    base = request.app["backend"].rstrip("/")
    try:
        async with request.app["session"].request(method, f"{base}{path}", json=body, timeout=aiohttp.ClientTimeout(total=170)) as r:
            return await r.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return {"ok": False, "error": "The PC didn't answer."}


def _text(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text[:2000]}], "isError": error}


async def call_tool(request: web.Request, device: str, name: str, args: dict) -> dict:
    if name == "mbuddy_status":
        o = await _backend(request, "GET", "/ui/overview")
        if not o.get("ok"):
            return _text("I couldn't read the status.", True)
        timers = ", ".join(f"{t.get('label')} ({round(t.get('remaining_s', 0) / 60, 1)} min left)" for t in o.get("timers", [])) or "no timers"
        did = "; ".join(str(a.get("result", ""))[:60] for a in (o.get("recent_actions") or [])[-3:]) or "nothing yet"
        return _text(f"Timers: {timers}. Recently: {did}. Volume: {o.get('volume') if o.get('volume') is not None else 'unknown'}.")
    if name == "mbuddy_tasks":
        t = await _backend(request, "GET", "/ui/tasks")
        if not t.get("ok"):
            return _text("I couldn't read the tasks.", True)
        rows = [f"{x.get('title')}" + (f" ({x.get('due_date')})" if x.get("due_date") else "") for x in (t.get("tasks") or [])[:15]]
        return _text("; ".join(rows) if rows else "There are no tasks.")
    if name == "mbuddy_add_task":
        title = str(args.get("title", "")).strip()[:200]
        if not title:
            return _text("A task needs a title.", True)
        r = await _backend(request, "POST", "/ui/tasks", {"title": title, "when": str(args.get("when", ""))[:80]})
        if r.get("ok"):
            await asyncio.to_thread(remote.log_action, device, f"Added a task (on-device model): {title[:50]}")
            return _text(f"Added: {title}.")
        return _text(str(r.get("error") or "I couldn't add that."), True)
    if name == "mbuddy_ask":
        q = str(args.get("question", "")).strip()[:1000]
        if not q:
            return _text("There was no question.", True)
        r = await _backend(request, "POST", "/control/chat", {"text": q, "speak": False})
        await asyncio.to_thread(remote.log_action, device, f"Asked (on-device model): {q[:50]}")
        return _text(str(r.get("reply") or r.get("error") or "No answer."), not r.get("ok"))
    return _text(f"There is no tool called {name}.", True)


async def mcp_handler(request: web.Request) -> web.Response:
    if request.method != "POST":
        return web.Response(status=405, headers={"Allow": "POST"})            # no server-initiated stream here
    if request.headers.get("Origin"):                                          # a browser page: refuse (DNS rebinding)
        return web.Response(status=403, text="Not for web pages.")
    if remote.paused():
        return web.Response(status=423, text="Remote access is paused on the PC.")
    device = await asyncio.to_thread(remote.verify, request.match_info["token"])
    if device is None:
        return web.Response(status=401, text="Not paired.")
    try:
        msg = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error", 400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid request", 400)
    method, id_ = msg.get("method"), msg.get("id")
    if id_ is None:                                                            # a notification: accepted, no body
        return web.Response(status=202)
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        asked = str(params.get("protocolVersion", ""))
        return _result(id_, {"protocolVersion": asked if asked in SUPPORTED else SUPPORTED[0],
                             "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER})
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name", ""))
        if name not in {t["name"] for t in TOOLS}:
            return _rpc_error(id_, -32602, f"Unknown tool: {name}")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        log.info(f"[Remote] MCP {name} from {device['name']}")
        return _result(id_, await call_tool(request, device["name"], name, args))
    return _rpc_error(id_, -32601, f"Method not found: {method}")
