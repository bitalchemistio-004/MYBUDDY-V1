"""Pairing and access for phones that reach MBUDDY from outside the PC.

What it keeps (~/.buddy/config/remote.json, never the tokens themselves):
- the paired phones: an id, a name, and the SHA-256 of the phone's token;
- whether remote access is paused (the kill switch);
- a pairing code that is shown on the PC, lives five minutes and works once.

The rules this file enforces:
- A phone is paired only with the code shown on this PC. Five wrong codes in a
  row lock pairing for five minutes.
- A token is made once, handed to the phone, and only its hash is stored, so a
  copy of the file cannot be used to get in. Tokens are compared in constant time.
- Pausing cuts every phone off at once; they stay paired and come back when it
  is resumed. Revoking removes a phone for good.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

CODE_TTL_S = 300
MAX_BAD_CODES = 5
LOCK_S = 300
_lock = threading.RLock()
_code: dict = {"value": "", "expires": 0.0}
_bad = {"count": 0, "locked_until": 0.0}


def _path() -> Path:
    override = os.getenv("MBUDDY_REMOTE_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "config" / "remote.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("paused", False)
    data.setdefault("lan", False)           # also listen on the local network (plain HTTP): off unless chosen
    data.setdefault("devices", [])
    return data


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── the kill switch and the network choice ───────────────────────────────
def paused() -> bool:
    with _lock:
        return bool(_load()["paused"])


def set_paused(value: bool) -> None:
    with _lock:
        data = _load()
        data["paused"] = bool(value)
        _save(data)


def lan_enabled() -> bool:
    with _lock:
        return bool(_load()["lan"])


def set_lan(value: bool) -> None:
    with _lock:
        data = _load()
        data["lan"] = bool(value)
        _save(data)


# ── pairing ──────────────────────────────────────────────────────────────
def new_code() -> dict:
    """A fresh six-digit code, shown on the PC. Replaces any earlier one."""
    with _lock:
        _code["value"] = f"{secrets.randbelow(10**6):06d}"
        _code["expires"] = time.time() + CODE_TTL_S
        return {"code": _code["value"], "expires": _code["expires"]}


def current_code() -> dict | None:
    with _lock:
        if _code["value"] and time.time() < _code["expires"]:
            return {"code": _code["value"], "expires": _code["expires"]}
        return None


def pair(code: str, name: str) -> tuple[str | None, str]:
    """(token, "") for a right code, else (None, why). The code works once."""
    with _lock:
        now = time.time()
        if now < _bad["locked_until"]:
            return None, "Too many wrong codes. Try again in a few minutes."
        live = current_code()
        if live is None:
            return None, "There is no code. Ask for a new one on the PC."
        if not hmac.compare_digest(str(code).strip(), live["code"]):
            _bad["count"] += 1
            if _bad["count"] >= MAX_BAD_CODES:
                _bad["locked_until"] = now + LOCK_S
                _bad["count"] = 0
                _code["value"] = ""
                return None, "Too many wrong codes. Pairing is locked for a few minutes."
            return None, "That code isn't right."
        _bad["count"] = 0
        _code["value"] = ""                                    # used once
        token = secrets.token_urlsafe(32)
        data = _load()
        clean = " ".join(str(name or "Phone").split())[:40] or "Phone"
        data["devices"].append({"id": secrets.token_hex(4), "name": clean, "hash": _hash(token), "created": now, "last_seen": now})
        _save(data)
        return token, ""


def verify(token: str) -> dict | None:
    """The paired phone this token belongs to (without its hash), or None. Refused while paused."""
    if not token:
        return None
    with _lock:
        data = _load()
        if data["paused"]:
            return None
        digest = _hash(token)
        for d in data["devices"]:
            if hmac.compare_digest(d["hash"], digest):
                now = time.time()
                if now - d.get("last_seen", 0) > 30:
                    d["last_seen"] = now
                    _save(data)
                return {k: v for k, v in d.items() if k != "hash"}
        return None


def revoke(device_id: str) -> bool:
    with _lock:
        data = _load()
        keep = [d for d in data["devices"] if d["id"] != device_id]
        if len(keep) == len(data["devices"]):
            return False
        data["devices"] = keep
        _save(data)
        return True


# ── what the phones did (for the Away screen; stays on this PC) ─────────────
def _log_path() -> Path:
    return _path().with_name("remote_log.jsonl")


def log_action(device: str, what: str) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "device": device, "what": what[:120]}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent_actions(n: int = 10) -> list[dict]:
    try:
        lines = _log_path().read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def state() -> dict:
    """What the Away screen shows. Never contains a token or a hash."""
    with _lock:
        data = _load()
        return {"paused": data["paused"], "lan": data["lan"], "code": current_code(), "actions": recent_actions(10),
                "devices": [{"id": d["id"], "name": d["name"], "created": d["created"], "last_seen": d.get("last_seen", 0)} for d in data["devices"]]}
