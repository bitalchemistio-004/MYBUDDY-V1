"""Deterministic companion memory helpers for projects, reminders, and daily stats."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any


REMINDER_RE = re.compile(
    r"\b(remind me|don't forget|dont forget|we need to|we should|later today|tomorrow)\b",
    re.IGNORECASE,
)

PROJECT_RE = re.compile(
    r"\b(project|firmware|esp32|brain|memory|voice|oled|websocket|cleanup|test|bug|feature|build|system)\b",
    re.IGNORECASE,
)


def ensure_companion_memory(memory: dict[str, Any]) -> dict[str, Any]:
    memory.setdefault("project_threads", [])
    memory.setdefault("reminders", [])
    memory.setdefault("daily_summaries", [])
    return memory


def maybe_capture_reminder(user_text: str, memory: dict[str, Any]) -> bool:
    ensure_companion_memory(memory)
    text = " ".join(user_text.strip().split())
    if not text or "?" in text or not REMINDER_RE.search(text):
        return False
    item = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "text": text[:180],
        "status": "open",
    }
    existing = {r.get("text", "").lower() for r in memory["reminders"]}
    if item["text"].lower() in existing:
        return False
    memory["reminders"].append(item)
    memory["reminders"] = memory["reminders"][-30:]
    return True


def maybe_update_project_thread(user_text: str, reply: str, memory: dict[str, Any]) -> bool:
    ensure_companion_memory(memory)
    joined = f"{user_text} {reply}"
    if not PROJECT_RE.search(joined):
        return False
    title = _project_title(user_text)
    item = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "title": title,
        "last_user": user_text[:180],
        "last_mbuddy": reply[:180],
        "status": "active",
    }
    threads = memory["project_threads"]
    if threads and threads[-1].get("title") == title:
        threads[-1].update(item)
    else:
        threads.append(item)
    memory["project_threads"] = threads[-20:]
    return True


def build_daily_stats(memory: dict[str, Any]) -> dict[str, Any]:
    ensure_companion_memory(memory)
    today = datetime.now().strftime("%Y-%m-%d")
    conversations = [
        e for e in memory.get("conversation_log", [])
        if str(e.get("time", "")).startswith(today)
    ]
    open_reminders = [r for r in memory.get("reminders", []) if r.get("status") == "open"]
    active_thread = ""
    if memory.get("project_threads"):
        active_thread = memory["project_threads"][-1].get("title", "")
    return {
        "date": today,
        "conversation_count": len(conversations),
        "open_reminders": len(open_reminders),
        "inner_thoughts": len(memory.get("inner_log", [])),
        "active_project_thread": active_thread,
    }


def maybe_write_daily_summary(memory: dict[str, Any], state=None) -> bool:
    ensure_companion_memory(memory)
    stats = build_daily_stats(memory)
    summaries = memory["daily_summaries"]
    if summaries and summaries[-1].get("date") == stats["date"]:
        summaries[-1].update(stats)
        if state is not None:
            summaries[-1]["mood"] = getattr(state, "current", "Normal")
            summaries[-1]["energy"] = round(float(getattr(state, "energy", 0.0)), 1)
        return False
    entry = {
        **stats,
        "mood": getattr(state, "current", "Normal") if state is not None else "Normal",
        "energy": round(float(getattr(state, "energy", 0.0)), 1) if state is not None else 0.0,
        "suggested_next_step": _suggest_next_step(memory),
    }
    summaries.append(entry)
    memory["daily_summaries"] = summaries[-14:]
    return True


def _project_title(text: str) -> str:
    lowered = text.lower()
    if "firmware" in lowered or "esp32" in lowered or "oled" in lowered:
        return "firmware and hardware behavior"
    if "memory" in lowered or "remind" in lowered:
        return "memory and reminders"
    if "voice" in lowered or "tts" in lowered or "audio" in lowered:
        return "voice and audio pipeline"
    if "cleanup" in lowered or "test" in lowered:
        return "code cleanup and tests"
    return "MBUDDY project"


def _suggest_next_step(memory: dict[str, Any]) -> str:
    open_reminders = [r for r in memory.get("reminders", []) if r.get("status") == "open"]
    if open_reminders:
        return open_reminders[-1].get("text", "")[:160]
    if memory.get("project_threads"):
        return f"Continue {memory['project_threads'][-1].get('title', 'the active project')}."
    return "Keep the next interaction lightweight and useful."
