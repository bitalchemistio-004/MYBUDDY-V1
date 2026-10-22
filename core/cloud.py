"""The bigger model in the cloud (Ollama Cloud), used only when the local 4B can't.

What this module does, and what it never does:
- It reads the key from the environment (OLLAMA_CLOUD_API_KEY, loaded from
  .env) and never prints, logs or returns any part of it.
- It is off unless the user's settings turn it on ("cloud"), and a model that
  answers "not in your plan" (402) or "not rolled out" (403) is left alone for
  a while instead of being retried on every turn.
- Every call is written to a usage log (~/.buddy/cloud_usage.jsonl), so the
  Models screen can show what was sent, to which model, and what it cost in
  tokens. A daily token cap in the settings stops calls when it is reached.
- Nothing from the PC reaches it unless the ability that asked it did so after
  the user's yes (brain/connectors/cloud.py).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

import utils.logger as log

DEFAULT_BASE = "https://ollama.com"
DEFAULT_SETTINGS = {"enabled": True, "model": "gpt-oss:120b", "vision_model": "gemma4:31b", "brain_model": "gemma4:31b", "daily_token_cap": 0}
UNAVAILABLE_FOR_S = 600          # a model that said 402/403 is not asked again for ten minutes
_lock = threading.Lock()
_unavailable: dict[str, tuple[float, str]] = {}


class CloudError(Exception):
    """Why a cloud call did not happen or did not work; the text is safe to say to the user."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind          # off | no_key | offline | plan | rollout | rate | cap | bad


@dataclass
class CloudReply:
    text: str
    model: str
    secs: float
    tokens_in: int
    tokens_out: int


# ── settings and the key ─────────────────────────────────────────────────
KEY_NAME = "ollama_cloud_key"


def _key() -> str:
    """The key the user saved in the app (encrypted for this Windows account), else the one in .env."""
    from core import secret_store
    return secret_store.load(KEY_NAME).strip() or os.getenv("OLLAMA_CLOUD_API_KEY", "").strip().strip('"')


def key_source() -> str:
    """"app" | "env" | "" — where the key comes from, never the key."""
    from core import secret_store
    if secret_store.load(KEY_NAME).strip():
        return "app"
    return "env" if os.getenv("OLLAMA_CLOUD_API_KEY", "").strip() else ""


def auth_header() -> dict:
    return {"Authorization": f"Bearer {_key()}"}


def _base() -> str:
    return os.getenv("OLLAMA_CLOUD_BASE_URL", DEFAULT_BASE).rstrip("/")


def settings() -> dict:
    from core import ui_settings
    stored = ui_settings.get("cloud") or {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}


def model_for(kind: str = "main") -> str:
    s = settings()
    return {"vision": s["vision_model"], "brain": s["brain_model"]}.get(kind, s["model"])


def key_present() -> bool:
    return bool(_key())


def enabled() -> bool:
    return bool(settings()["enabled"]) and key_present()


def status() -> dict:
    """What the Models screen shows. Never contains the key."""
    s = settings()
    now = time.time()
    with _lock:
        blocked = {m: why for m, (until, why) in _unavailable.items() if until > now}
    return {"key_present": key_present(), "enabled": bool(s["enabled"]), "usable": enabled(), "model": s["model"],
            "vision_model": s["vision_model"], "brain_model": s["brain_model"], "key_source": key_source(), "daily_token_cap": s["daily_token_cap"], "blocked": blocked,
            "base": _base()}


# ── usage log ────────────────────────────────────────────────────────────
def _usage_path() -> Path:
    override = os.getenv("MBUDDY_CLOUD_USAGE_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "cloud_usage.jsonl"


def _record(row: dict) -> None:
    try:
        path = _usage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warn(f"[Cloud] could not write the usage log: {exc}")


def usage(days: int = 1) -> dict:
    """Calls and tokens in the last `days` days (1 = since midnight today is not needed: a rolling 24 h)."""
    cutoff = time.time() - days * 86400
    path = _usage_path()
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("t", 0) >= cutoff:
                rows.append(r)
    by: dict[str, dict] = {}
    for r in rows:
        m = by.setdefault(r.get("model", "?"), {"calls": 0, "tokens_in": 0, "tokens_out": 0})
        m["calls"] += 1
        m["tokens_in"] += int(r.get("in", 0))
        m["tokens_out"] += int(r.get("out", 0))
    return {"days": days, "calls": len(rows), "tokens_in": sum(int(r.get("in", 0)) for r in rows),
            "tokens_out": sum(int(r.get("out", 0)) for r in rows), "failed": sum(1 for r in rows if not r.get("ok", True)),
            "by_model": by, "recent": rows[-8:][::-1]}


def last_call() -> dict | None:
    path = _usage_path()
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ok", True):
            return r
    return None


def _over_cap() -> bool:
    cap = int(settings().get("daily_token_cap") or 0)
    if cap <= 0:
        return False
    u = usage(1)
    return u["tokens_in"] + u["tokens_out"] >= cap


# ── the call ─────────────────────────────────────────────────────────────
def check_usable(kind: str = "main", model: str | None = None) -> str:
    """"" when a call may be made now, else why not (words for the user)."""
    if not key_present():
        return "No cloud key is set up."
    if not settings()["enabled"]:
        return "The bigger model is switched off in Settings, Models."
    m = model or model_for(kind)
    now = time.time()
    with _lock:
        until, why = _unavailable.get(m, (0, ""))
    if until > now:
        return why
    if _over_cap():
        return "Today's cloud limit is used up."
    return ""


def chat(messages: list[dict], *, kind: str = "main", purpose: str = "", images: list[str] | None = None,
         max_tokens: int = 1500, timeout: float = 90.0, model: str | None = None) -> CloudReply:
    """One request. Raises CloudError with a plain reason; records every call. `model` names one model
    explicitly (the preview on the Models screen); otherwise the chosen one for `kind` is used."""
    why = check_usable(kind, model)
    if why:
        raise CloudError("off" if "switched off" in why else "no_key" if "key" in why else "cap" if "limit" in why else "plan", why)
    model = model or model_for(kind)
    msgs = [dict(m) for m in messages]
    if images:
        for m in reversed(msgs):
            if m.get("role") == "user":
                m["images"] = images
                break
    body = {"model": model, "stream": False, "messages": msgs, "options": {"temperature": 0.2, "num_predict": max_tokens}}
    started = time.time()
    try:
        r = requests.post(f"{_base()}/api/chat", json=body, headers={"Authorization": f"Bearer {_key()}"}, timeout=timeout)
    except requests.RequestException as exc:
        _record({"t": started, "model": model, "purpose": purpose, "in": 0, "out": 0, "secs": round(time.time() - started, 1), "ok": False, "why": type(exc).__name__})
        raise CloudError("offline", "I can't reach the cloud right now.") from exc
    secs = round(time.time() - started, 1)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200:
        err = str(data.get("error", ""))[:160]
        kind_ = {402: "plan", 403: "rollout", 429: "rate", 401: "no_key"}.get(r.status_code, "bad")
        said = {"plan": f"{model} isn't on this cloud plan.", "rollout": f"{model} isn't available to this account yet.",
                "rate": "The cloud is busy or the limit was reached.", "no_key": "The cloud key was refused.",
                "bad": "The cloud answered with an error."}[kind_]
        if kind_ in ("plan", "rollout", "no_key"):
            with _lock:
                _unavailable[model] = (time.time() + UNAVAILABLE_FOR_S, said)
        _record({"t": started, "model": model, "purpose": purpose, "in": 0, "out": 0, "secs": secs, "ok": False, "why": f"HTTP {r.status_code}"})
        log.warn(f"[Cloud] {model}: HTTP {r.status_code} {err[:80]}")
        raise CloudError(kind_, said)
    msg = data.get("message") or {}
    text = str(msg.get("content", "")).strip()
    calls = msg.get("tool_calls") or []
    if not text and calls:      # some models answer through the native tool-call field: same words, other channel
        fn = calls[0].get("function") or {}
        text = json.dumps({"tool": fn.get("name"), "args": fn.get("arguments") or {}})
    if not text:
        _record({"t": started, "model": model, "purpose": purpose, "in": int(data.get("prompt_eval_count", 0)), "out": int(data.get("eval_count", 0)),
                 "secs": secs, "ok": False, "why": "empty answer"})
        raise CloudError("bad", "The bigger model gave no answer.")
    reply = CloudReply(text, model, secs, int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)))
    _record({"t": started, "model": model, "purpose": purpose, "in": reply.tokens_in, "out": reply.tokens_out, "secs": secs, "ok": True})
    return reply


def probe() -> dict:
    """One tiny, non-personal request, for the Models screen's test button."""
    try:
        r = chat([{"role": "user", "content": "Reply with exactly one word: OK"}], purpose="test", max_tokens=60, timeout=40)
        return {"ok": True, "model": r.model, "secs": r.secs, "reply": r.text[:30]}
    except CloudError as exc:
        return {"ok": False, "why": str(exc), "kind": exc.kind}


def via_text(rec: dict | None) -> str:
    """The line under a reply that came from the cloud, in words."""
    if not rec:
        return ""
    return f"CLOUD {rec.get('model', '?')} · {rec.get('secs', 0)} s · {rec.get('in', 0)} in, {rec.get('out', 0)} out"


# ── the user's own key, the model list, the plan check ───────────────────
def record_call(model: str, purpose: str, tokens_in: int, tokens_out: int, secs: float, ok: bool = True, why: str = "") -> None:
    """Meter a call made by someone else (the cloud-brain proxy)."""
    row = {"t": time.time() - secs, "model": model, "purpose": purpose, "in": int(tokens_in), "out": int(tokens_out), "secs": round(secs, 1), "ok": ok}
    if why:
        row["why"] = why
    _record(row)


def validate_key(key: str, model: str | None = None) -> dict:
    """Try a key with one tiny request. {"valid": bool, "model_ok": bool, "why": str}. The key is not stored here."""
    key = (key or "").strip().strip('"')
    if len(key) < 20 or any(c.isspace() for c in key):
        return {"valid": False, "model_ok": False, "why": "That doesn't look like an Ollama key (it is a long string with no spaces)."}
    model = model or settings()["brain_model"]
    try:
        r = requests.post(f"{_base()}/api/chat", headers={"Authorization": f"Bearer {key}"}, timeout=40,
                          json={"model": model, "stream": False, "messages": [{"role": "user", "content": "Reply with: OK"}],
                                "options": {"num_predict": 20, "temperature": 0}})
    except requests.RequestException:
        return {"valid": False, "model_ok": False, "why": "I can't reach ollama.com. Check the internet connection and try again."}
    if r.status_code == 200:
        try:
            d = r.json()
        except ValueError:
            d = {}
        record_call(model, "key check", d.get("prompt_eval_count", 0), d.get("eval_count", 0), 0.0)
        return {"valid": True, "model_ok": True, "why": ""}
    if r.status_code == 401:
        return {"valid": False, "model_ok": False, "why": "Ollama refused this key. Copy it again from ollama.com/settings/keys."}
    if r.status_code in (402, 403):
        return {"valid": True, "model_ok": False, "why": f"The key works, but {model} isn't on this plan. Pick another model."}
    return {"valid": False, "model_ok": False, "why": f"Ollama answered with an error (HTTP {r.status_code}). Try again in a minute."}


def secret_store_available() -> bool:
    from core import secret_store
    return secret_store.available()


def save_key(key: str) -> None:
    from core import secret_store
    secret_store.save(KEY_NAME, key.strip().strip('"'))
    with _lock:
        _unavailable.clear()


def remove_key() -> None:
    from core import secret_store
    secret_store.delete(KEY_NAME)


_catalog: dict = {"t": 0.0, "names": []}


def catalog(force: bool = False) -> list[str]:
    """Model names ollama.com lists for the cloud (cached 10 minutes). [] when it can't be read."""
    if not force and _catalog["names"] and time.time() - _catalog["t"] < 600:
        return list(_catalog["names"])
    try:
        r = requests.get(f"{_base()}/api/tags", timeout=15)
        names = sorted({m.get("name", "") for m in r.json().get("models", []) if m.get("name")})
    except (requests.RequestException, ValueError):
        return list(_catalog["names"])
    if names:
        _catalog.update(t=time.time(), names=names)
    return names


def check_plan(models: list[str]) -> dict:
    """For each model, does this key's plan allow it? One tiny request each (a few tokens), at most 8."""
    out = {}
    for m in models[:8]:
        try:
            r = requests.post(f"{_base()}/api/chat", headers=auth_header(), timeout=40,
                              json={"model": m, "stream": False, "messages": [{"role": "user", "content": "OK?"}],
                                    "options": {"num_predict": 8, "temperature": 0}})
        except requests.RequestException:
            out[m] = "offline"
            continue
        if r.status_code == 200:
            d = r.json() if r.content else {}
            record_call(m, "plan check", d.get("prompt_eval_count", 0), d.get("eval_count", 0), 0.0)
            out[m] = "ok"
        else:
            out[m] = {402: "not in plan", 403: "not available yet", 401: "key refused", 429: "busy"}.get(r.status_code, f"error {r.status_code}")
    return out


# ── the usage page ───────────────────────────────────────────────────────
# List prices in US dollars per million tokens (input, output), only where they were read from ollama.com.
# A model not listed shows no estimate: a guess would look like a fact. A free plan is not billed per token.
PRICES = {"gemma4:31b": (0.14, 0.40)}


def usage_report(days: int = 30) -> dict:
    """Everything the Usage page shows, from the usage log: totals, per model, per purpose, per day, recent calls."""
    days = max(1, min(int(days), 90))
    cutoff = time.time() - days * 86400
    rows = []
    path = _usage_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("t", 0) >= cutoff:
                rows.append(r)

    def total(rs: list[dict]) -> dict:
        return {"calls": len(rs), "tokens_in": sum(int(r.get("in", 0)) for r in rs), "tokens_out": sum(int(r.get("out", 0)) for r in rs),
                "failed": sum(1 for r in rs if not r.get("ok", True)), "secs": round(sum(float(r.get("secs", 0)) for r in rs), 1)}

    def group(keyfn) -> list[dict]:
        buckets: dict[str, list[dict]] = {}
        for r in rows:
            buckets.setdefault(keyfn(r), []).append(r)
        return sorted(({"name": k, **total(v)} for k, v in buckets.items()), key=lambda x: -(x["tokens_in"] + x["tokens_out"]))

    by_day = []
    for i in range(days - 1, -1, -1):
        t = time.time() - i * 86400
        label = time.strftime("%Y-%m-%d", time.localtime(t))
        rs = [r for r in rows if time.strftime("%Y-%m-%d", time.localtime(r.get("t", 0))) == label]
        by_day.append({"day": label, **total(rs)})
    by_model = group(lambda r: r.get("model", "?"))
    estimate, unpriced = 0.0, []
    for m in by_model:
        if m["name"] in PRICES:
            pin, pout = PRICES[m["name"]]
            m["usd"] = round((m["tokens_in"] * pin + m["tokens_out"] * pout) / 1e6, 4)
            estimate += m["usd"]
        else:
            m["usd"] = None
            unpriced.append(m["name"])
    s = settings()
    today = total([r for r in rows if r.get("t", 0) >= time.time() - 86400])
    return {"days": days, "total": total(rows), "today": today, "by_model": by_model, "by_purpose": group(lambda r: r.get("purpose") or "other"),
            "by_day": by_day, "recent": rows[-15:][::-1], "estimate_usd": round(estimate, 4), "unpriced": unpriced,
            "cap": int(s.get("daily_token_cap") or 0), "local_note": "Questions answered by the model on this PC are not counted here: they cost nothing."}


def preview(model: str, prompt: str) -> dict:
    """Ask one named model one short question, so the user can see how it answers before choosing it.
    Counted in the usage log as "preview". Never sends anything from this PC: only the typed prompt."""
    model = (model or "").strip()
    prompt = (prompt or "").strip()[:600]
    if not model or not prompt:
        return {"ok": False, "why": "Pick a model and write a short question."}
    try:
        r = chat([{"role": "user", "content": prompt}], purpose="preview", max_tokens=400, timeout=60, model=model)
    except CloudError as exc:
        return {"ok": False, "why": str(exc), "kind": exc.kind}
    price = PRICES.get(model)
    usd = round((r.tokens_in * price[0] + r.tokens_out * price[1]) / 1e6, 6) if price else None
    return {"ok": True, "model": r.model, "secs": r.secs, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "reply": r.text[:1500], "usd": usd}
