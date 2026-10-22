"""api/remote_api.py - the Away screen's side of phone access (core/remote.py, api/remote_gateway.py).
Local only: the PC's own app pairs, pauses and removes phones. A phone can never call this."""
from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess

from aiohttp import web

from api.device_control import _require_local
from core import remote, ui_settings


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def _tailscale() -> dict:
    """Whether Tailscale is installed here and this PC's private name, read from its own command (read only)."""
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=4, creationflags=0x08000000)
        data = json.loads(out.stdout or "{}")
        name = str((data.get("Self") or {}).get("DNSName", "")).rstrip(".")
        return {"installed": True, "running": data.get("BackendState") == "Running", "name": name}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"installed": False, "running": False, "name": ""}


def _state(request: web.Request) -> dict:
    s = remote.state()
    listening = request.app.get("remote_listening")
    return {"ok": True, **s, "gateway": {"listening": bool(listening), "host": listening[0] if listening else "", "port": listening[1] if listening else 0},
            "lan_ips": _lan_ips(), "tailscale": _tailscale(), "work_on": ui_settings.get("work_on")}


async def state_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(_state, request))


async def action_handler(request: web.Request) -> web.Response:
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "Send a JSON object."}, 400)
    action = str((data or {}).get("action", ""))
    if action == "new_code":
        await asyncio.to_thread(remote.new_code)
    elif action == "pause":
        await asyncio.to_thread(remote.set_paused, True)
    elif action == "resume":
        await asyncio.to_thread(remote.set_paused, False)
    elif action == "revoke":
        if not await asyncio.to_thread(remote.revoke, str(data.get("id", ""))):
            return _json({"ok": False, "error": "No such phone."}, 404)
    elif action == "lan":
        await asyncio.to_thread(remote.set_lan, bool(data.get("value")))
    else:
        return _json({"ok": False, "error": "Unknown action."}, 400)
    return _json(await asyncio.to_thread(_state, request))


def register(app: web.Application) -> None:
    app.router.add_get("/ui/remote", state_handler)
    app.router.add_post("/ui/remote", action_handler)
