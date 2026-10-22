"""
brain/tools.py — Executable tool implementations for MBUDDY.

All tools advertised in capability_registry must have implementations here.
Each tool returns a structured response that can be spoken or displayed.
"""
from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import quote
import re
import time
from datetime import datetime

from core.time_manager import get_time_manager
from pathlib import Path
from typing import Any, Optional

import requests

import utils.logger as log


# ═══════════════════════════════════════════════════════════════════════════════
# TIMER TOOL
# ═══════════════════════════════════════════════════════════════════════════════

TIMERS_DB = Path("memory/timers.json")
FINISHED_GRACE_S = 3600  # announce timers that ran out while MBUDDY was off, up to an hour

class Timer:
    """Active timer with completion tracking."""

    def __init__(self, label: str, duration_seconds: int, created_at: float):
        self.label = label
        self.duration_seconds = duration_seconds
        self.created_at = created_at
        self.end_time = created_at + duration_seconds

    def remaining_seconds(self, now: float | None = None) -> float:
        current = now if now is not None else time.time()
        return max(0.0, self.end_time - current)

    def is_complete(self, now: float | None = None) -> bool:
        return self.remaining_seconds(now) <= 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at,
            "end_time": self.end_time,
            "remaining_seconds": self.remaining_seconds(),
            "is_complete": self.is_complete(),
        }


class TimerManager:
    """Manages multiple concurrent timers."""

    def __init__(self, db_path: Path = TIMERS_DB):
        self.db_path = db_path
        self.timers: dict[str, Timer] = {}
        self._load()

    def _load(self) -> None:
        """Load timers from disk."""
        if not self.db_path.exists():
            return
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                timer = Timer(
                    label=item["label"],
                    duration_seconds=item["duration_seconds"],
                    created_at=item["created_at"]
                )
                # Keep timers that finished within the last hour (e.g. while
                # MBUDDY was closed) so pop_finished() announces them once.
                if timer.end_time > time.time() - FINISHED_GRACE_S:
                    self.timers[timer.label] = timer
            self._save()  # Clean up older completed timers
        except Exception as exc:
            log.warn(f"Failed to load timers: {exc}")

    def _save(self) -> None:
        """Persist active timers to disk."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = [t.to_dict() for t in self.timers.values() if not t.is_complete()]
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log.error(f"Failed to save timers: {exc}")

    def start_timer(self, label: str, duration_seconds: int) -> Timer:
        """Start a new timer."""
        timer = Timer(label, duration_seconds, time.time())
        self.timers[label] = timer
        self._save()
        log.info(f"Started timer: {label} ({duration_seconds}s)")
        return timer

    def get_timer(self, label: str) -> Timer | None:
        """Get a timer by label."""
        return self.timers.get(label)

    def list_timers(self) -> list[Timer]:
        """List all active timers."""
        # Finished timers stay until pop_finished() announces them; listing
        # used to delete them, which could swallow the announcement.
        return [t for t in self.timers.values() if not t.is_complete()]

    def pop_finished(self, now: float | None = None) -> list[Timer]:
        """
        Timers that have run out, each returned once and then forgotten.

        This is what announces a timer: before it existed a finished timer
        simply expired, and nothing ever told the user.
        """
        current = now if now is not None else time.time()
        done = [t for t in self.timers.values() if t.is_complete(current)]
        if done:
            for t in done:
                del self.timers[t.label]
            self._save()
        return done

    def cancel_timer(self, label: str) -> bool:
        """Cancel a timer."""
        if label in self.timers:
            del self.timers[label]
            self._save()
            log.info(f"Cancelled timer: {label}")
            return True
        return False


_timer_manager = TimerManager()


def parse_duration(text: str) -> Optional[int]:
    """Parse duration from natural language to seconds."""
    text = text.lower().strip()

    # Pattern: "X seconds/minutes/hours"
    match = re.search(r'(\d+)\s*(second|seconds|sec|secs|s|minute|minutes|min|mins|m|hour|hours|hr|hrs|h)', text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)

        if unit in ('second', 'seconds', 'sec', 'secs', 's'):
            return amount
        elif unit in ('minute', 'minutes', 'min', 'mins', 'm'):
            return amount * 60
        elif unit in ('hour', 'hours', 'hr', 'hrs', 'h'):
            return amount * 3600

    return None


def execute_timer_tool(query: str) -> str:
    """Execute timer operations based on natural language query."""
    query_lower = query.lower()

    # List timers
    if any(word in query_lower for word in ['list', 'show', 'what', 'check']):
        timers = _timer_manager.list_timers()
        if not timers:
            return "No active timers right now."

        lines = ["Active timers:"]
        for timer in timers:
            remaining = timer.remaining_seconds()
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            if mins > 0:
                lines.append(f"• {timer.label}: {mins}m {secs}s remaining")
            else:
                lines.append(f"• {timer.label}: {secs}s remaining")
        return "\n".join(lines)

    # Cancel timer
    if any(word in query_lower for word in ['cancel', 'stop', 'delete', 'remove']):
        # Extract label
        for word in ['cancel', 'stop', 'delete', 'remove']:
            if word in query_lower:
                label = query_lower.split(word)[-1].strip()
                break
        else:
            label = query_lower.strip()

        if _timer_manager.cancel_timer(label):
            return f"Cancelled timer: {label}"
        else:
            return f"No timer found with label: {label}"

    # Start timer - pattern: "set timer for X" or "X minute timer" or "timer for X"
    duration = parse_duration(query)
    if duration is None:
        return "I couldn't understand the timer duration. Try something like 'set a timer for 5 minutes'."

    # Extract label (everything before duration phrase, or use default)
    label_match = re.search(r'(.+?)(?:\d+\s*(?:second|minute|hour))', query_lower)
    if label_match:
        label = label_match.group(1).strip()
        label = re.sub(r'\b(set|start|create|make|a|an|the|timer|for)\b', '', label).strip()
    else:
        label = "timer"

    if not label:
        label = f"{duration}s timer"

    timer = _timer_manager.start_timer(label, duration)
    mins = duration // 60
    secs = duration % 60

    if mins > 0:
        time_str = f"{mins} minute{'s' if mins != 1 else ''}"
        if secs > 0:
            time_str += f" and {secs} second{'s' if secs != 1 else ''}"
    else:
        time_str = f"{secs} second{'s' if secs != 1 else ''}"

    return f"Timer started: {label} for {time_str}"


def timer_name(t) -> str:
    """A timer as people say it: "pasta", or "2 minute" for unnamed ones."""
    label = str(t.label or "").strip()
    if not label or re.fullmatch(r"(\d+s )?timer", label):
        secs = int(t.duration_seconds)
        label = f"{secs // 60} minute" if secs >= 60 and secs % 60 == 0 else f"{secs} second"
    return label


def timer_announcement(timers) -> str:
    """What MBUDDY says when timers run out; empty when none did."""
    if not timers:
        return ""
    names = [timer_name(t) for t in timers]
    if len(names) == 1:
        return f"[NORMAL] Hey, your {names[0]} timer is done."
    return f"[NORMAL] Hey, your timers are done: {', '.join(names[:-1])} and {names[-1]}."


# ═══════════════════════════════════════════════════════════════════════════════
# TASK TOOL
# ═══════════════════════════════════════════════════════════════════════════════

def execute_task_tool(query: str) -> str:
    """Execute task-tracker operations based on natural language query."""
    from brain.task_tracker import task_tracker, parse_task_from_message, format_task_list

    query_lower = query.lower().strip()

    # Add to the list — checked before listing, because "add milk to my task
    # list" contains the word "list" and used to list tasks instead of adding.
    add = re.match(
        r"^\s*(?:please\s+)?(?:add|put)\s+(.+?)\s+(?:to|on(?:to)?|in)\s+(?:my\s+)?(?:task|to-?do|todo)?\s*list\W*$",
        query, flags=re.IGNORECASE,
    )
    if add:
        task = task_tracker.create_task(description=add.group(1).strip())
        return f"Added task #{task.task_id}: {task.title}"

    # List tasks
    if _re_search_any(query_lower, ['list', 'show', 'what', 'my tasks', 'pending', 'to-do', 'todo']):
        tasks = task_tracker.get_pending_tasks()
        return format_task_list(tasks)

    # Complete a task — "mark X as done", "complete X", "finished X"
    match = re.search(r'(?:mark|complete|finish(?:ed)?)\s+(.+?)(?:\s+as\s+(?:done|complete|finished))?$', query_lower)
    if match and _re_search_any(query_lower, ['done', 'complete', 'finish']):
        target = match.group(1).strip()
        id_match = re.search(r'#?(\d+)$', target)
        if id_match:
            task_id = int(id_match.group(1))
        else:
            found = task_tracker.search_tasks(target)
            if not found:
                return f"I couldn't find a pending task matching '{target}'."
            task_id = found[0].task_id
        if task_tracker.complete_task(task_id):
            return f"Marked task #{task_id} as complete."
        return f"No pending task found with id #{task_id}."

    # Delete a task
    match = re.search(r'(?:delete|remove|cancel)\s+task\s+(.+)', query_lower)
    if match:
        target = match.group(1).strip()
        id_match = re.search(r'#?(\d+)$', target)
        if id_match:
            task_id = int(id_match.group(1))
        else:
            found = task_tracker.search_tasks(target)
            if not found:
                return f"I couldn't find a pending task matching '{target}'."
            task_id = found[0].task_id
        if task_tracker.delete_task(task_id):
            return f"Deleted task #{task_id}."
        return f"No task found with id #{task_id}."

    # Create a task — parse natural language, falling back to the raw text
    # verbatim so an unrecognized phrasing still gets tracked rather than
    # silently dropped.
    parsed = parse_task_from_message(query)
    if parsed is None:
        description = re.sub(r'^\s*(please\s+)?(add\s+(a\s+)?task[:\s]*|remind\s+me\s+to\s+)', '', query, flags=re.IGNORECASE).strip()
        if not description:
            return "I couldn't understand the task. Try something like 'remind me to call mom tomorrow at 5pm'."
        parsed = {"description": description, "due_date": None, "priority": "normal"}

    task = task_tracker.create_task(
        description=parsed["description"],
        due_date=parsed.get("due_date"),
        priority=parsed.get("priority", "normal"),
    )
    due_str = f" (due {task.due_date})" if task.due_date else ""
    return f"Added task #{task.task_id}: {task.title}{due_str}"


def _re_search_any(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


# ═══════════════════════════════════════════════════════════════════════════════
# CALCULATOR TOOL
# ═══════════════════════════════════════════════════════════════════════════════

def execute_calculator_tool(expression: str) -> str:
    """Safely evaluate mathematical expressions."""

    # Clean the expression
    expression = expression.strip()

    # Remove natural language words
    expression = re.sub(r'\b(what is|calculate|compute|solve|equals?|equal to)\b', '', expression, flags=re.IGNORECASE)
    expression = expression.strip()

    # "15 percent of 240" and "half of 80" are the two phrasings people
    # actually speak; without these the sanitiser below collapses them into
    # unparseable runs like "15percentof240".
    pct = re.search(
        r'([\d.]+)\s*(?:%|percent(?:age)?)\s*(?:of|off|from)\s*([\d.]+)',
        expression, flags=re.IGNORECASE,
    )
    if pct:
        expression = f"({pct.group(1)}/100)*{pct.group(2)}"
    else:
        frac = re.search(
            r'\b(half|a third|a quarter|two thirds|three quarters)\s+of\s+([\d.]+)',
            expression, flags=re.IGNORECASE,
        )
        if frac:
            factors = {
                'half': '0.5', 'a third': '(1/3)', 'a quarter': '0.25',
                'two thirds': '(2/3)', 'three quarters': '0.75',
            }
            expression = f"{factors[frac.group(1).lower()]}*{frac.group(2)}"

    # Replace common words with operators
    replacements = {
        ' plus ': '+',
        ' minus ': '-',
        ' times ': '*',
        ' multiplied by ': '*',
        ' divided by ': '/',
        ' over ': '/',
        ' mod ': '%',
        ' modulo ': '%',
        ' to the power of ': '**',
        ' squared': '**2',
        ' cubed': '**3',
    }

    for word, op in replacements.items():
        expression = expression.replace(word, op)

    # Remove spaces except in numbers like "1 000"
    expression = re.sub(r'(?<=\d)\s+(?=\d)', '', expression)
    expression = ''.join(expression.split())

    # Validate safe characters only
    if not re.match(r'^[\d+\-*/(). %**]+$', expression):
        return f"I can only calculate basic math expressions. '{expression}' contains unsupported characters."

    try:
        # Use eval with restricted namespace for safety
        safe_dict = {
            "__builtins__": {},
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "pow": pow,
        }
        result = eval(expression, safe_dict, {})

        # Format result nicely
        if isinstance(result, float):
            if result.is_integer():
                result = int(result)
            else:
                result = round(result, 6)

        return f"{expression} = {result}"

    except ZeroDivisionError:
        return "Cannot divide by zero."
    except Exception as exc:
        log.warn(f"Calculator error: {exc}")
        return "I couldn't calculate that. Make sure the expression is valid math."


# ═══════════════════════════════════════════════════════════════════════════════
# WEATHER TOOL
# ═══════════════════════════════════════════════════════════════════════════════

class ToolFailure(str):
    """
    A tool result that is an explanation of failure, not an answer.

    Subclasses str so every existing caller keeps working, while callers that
    care can tell the two apart. Without this a failure message reads exactly
    like data: the router logged success and handed the apology to the model
    as though it were a live reading.
    """


# Country names that also name towns elsewhere. Geocoders resolve the bare
# word to the town, so a question about the country comes back with another
# country's weather stated as fact.
AMBIGUOUS_COUNTRIES = {
    "lebanon": "Lebanon,LB",
    "georgia": "Tbilisi,GE",
    "jordan": "Amman,JO",
    "mexico": "Mexico City,MX",
    "chile": "Santiago,CL",
    "china": "Beijing,CN",
    "guatemala": "Guatemala City,GT",
    "kuwait": "Kuwait City,KW",
    "panama": "Panama City,PA",
    "singapore": "Singapore,SG",
    "qatar": "Doha,QA",
    "syria": "Damascus,SY",
    "peru": "Lima,PE",
    "brazil": "Brasilia,BR",
    "india": "New Delhi,IN",
    "canada": "Ottawa,CA",
}

# Trailing words that belong to the question, not to the place name.
_LOCATION_NOISE = re.compile(
    r"\s*\b(right now|now|today|tonight|tomorrow|currently|at the moment|"
    r"this morning|this afternoon|this evening|please|like|outside)\b\s*$",
    re.IGNORECASE,
)


def extract_location(query: str) -> str:
    """
    Pull the place name out of a weather question.

    Args:
        query: the user's sentence, e.g. "what's the weather in Lebanon right now?"

    Returns:
        A location string suitable for a geocoder, disambiguated where the
        bare name would resolve to the wrong country. Empty when the question
        names no place, which lets the provider fall back to IP location.
    """
    text = (query or "").strip()
    match = re.search(r"\b(?:in|for|at|over)\s+(.+)$", text, re.IGNORECASE)
    location = match.group(1) if match else ""

    location = location.strip().rstrip("?.!,؟ ")
    # "in Lebanon right now" -> "Lebanon". Repeat, so "in Beirut today please"
    # sheds both trailing words.
    while True:
        stripped = _LOCATION_NOISE.sub("", location).strip().rstrip("?.!,؟ ")
        if stripped == location:
            break
        location = stripped

    return AMBIGUOUS_COUNTRIES.get(location.lower(), location)


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _forecast_date(day: str, today=None):
    """The date [day] means ("today", "tomorrow", "day after tomorrow", a weekday, or YYYY-MM-DD); None for now/unknown."""
    from datetime import date, timedelta
    today = today or date.today()
    d = (day or "").strip().lower()
    if d in ("", "now", "current", "right now"):
        return None
    if d == "today" or d == "tonight":
        return today
    if d in ("tomorrow", "tmrw", "tomorow"):
        return today + timedelta(days=1)
    if d in ("day after tomorrow", "the day after tomorrow", "overmorrow"):
        return today + timedelta(days=2)
    for i, name in enumerate(_WEEKDAYS):
        if name in d or name[:3] == d:
            ahead = (i - today.weekday()) % 7 or (7 if "next" in d else 0)
            return today + timedelta(days=ahead)
    try:
        return date.fromisoformat(d[:10])
    except ValueError:
        return "unknown"


def _forecast_line(data: dict, where: str, wanted) -> str:
    from datetime import date
    days = data.get("weather") or []
    for day in days:
        if day.get("date") == wanted.isoformat():
            hourly = day.get("hourly") or [{}]
            noon = hourly[min(4, len(hourly) - 1)]
            desc = ((noon.get("weatherDesc") or [{}])[0].get("value") or "").strip()
            rain = max((int(h.get("chanceofrain", 0) or 0) for h in hourly), default=0)
            return (f"Forecast for {where} on {wanted.strftime('%a %d %b')}: {desc}, "
                    f"{day.get('mintempC', '?')} to {day.get('maxtempC', '?')}°C, up to {rain}% chance of rain")
    last = days[-1].get("date") if days else ""
    return ToolFailure(f"I can only see the forecast {len(days)} days ahead" + (f" (until {date.fromisoformat(last).strftime('%a %d %b')})" if last else "")
                       + f", not {wanted.strftime('%a %d %b')}.")


def execute_weather_tool(location: str = "", day: str = "now") -> str:
    """The weather now, or the forecast for [day] (today, tomorrow, a weekday, a date) when one is asked for.
    A forecast is never presented as the current weather, nor the current weather as a forecast (the field test:
    "sunny tomorrow, 25" was the reading of that moment)."""
    wanted = _forecast_date(day)
    if wanted == "unknown":
        return ToolFailure(f"I didn't understand which day '{day}' is.")

    if not (location or "").strip():
        from core import ui_settings
        location = ui_settings.get("home_location")  # set in the app; empty = wttr.in's IP guess
    # wttr.in's JSON endpoint, because it names the place it actually
    # resolved. The one-line format echoes the query back verbatim, so a bad
    # geocode reads exactly like a good one.
    try:
        query = quote(location, safe=",")
        url = f"https://wttr.in/{query}?format=j1"
        response = requests.get(url, timeout=8, headers={"User-Agent": "MBUDDY/4.1"})
        response.raise_for_status()
        data = response.json()

        current = (data.get("current_condition") or [{}])[0]
        area = (data.get("nearest_area") or [{}])[0]

        resolved_area = ((area.get("areaName") or [{}])[0]).get("value", "").strip()
        resolved_region = ((area.get("region") or [{}])[0]).get("value", "").strip()
        resolved_country = ((area.get("country") or [{}])[0]).get("value", "").strip()

        if not current or not resolved_country:
            raise ValueError("no reading in response")

        # A place name that resolves into a different country than the one
        # asked for is a wrong match, not a reading of somewhere else.
        asked_country = location.split(",")[-1].strip().lower() if "," in location else ""
        if asked_country and len(asked_country) <= 3:
            # A country code was supplied deliberately, e.g. "Lebanon,LB".
            asked_name = location.split(",")[0].strip().lower()
            if (asked_name and asked_name not in resolved_country.lower()
                    and asked_name not in resolved_area.lower()
                    and asked_name not in resolved_region.lower()):
                return ToolFailure(
                    f"Weather lookup failed: asked for {location!r} but the "
                    f"provider resolved {resolved_area}, {resolved_country}."
                )

        where = ", ".join(p for p in (resolved_area, resolved_country) if p)
        if wanted is not None:
            return _forecast_line(data, where, wanted)
        return (
            f"Current weather in {where}: {current.get('weatherDesc', [{}])[0].get('value', '').strip()}, "
            f"{current.get('temp_C', '?')}°C (feels like {current.get('FeelsLikeC', '?')}°C), "
            f"humidity {current.get('humidity', '?')}%, "
            f"wind {current.get('windspeedKmph', '?')} km/h"
        )

    except Exception as exc:
        log.warn(f"wttr.in weather failed: {exc}")

    if wanted is not None:
        # the fallback below only knows the current weather: it must not stand in for a forecast
        return ToolFailure("The forecast service didn't answer, so I can't say what the weather will be.")
    # Fallback to OpenWeatherMap if API key is available
    api_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if api_key:
        try:
            if not location:
                # Try to get location from IP
                ip_resp = requests.get("https://ipapi.co/json/", timeout=3)
                if ip_resp.ok:
                    ip_data = ip_resp.json()
                    location = ip_data.get("city", "")

            if not location:
                return "I need a location to check the weather. Try asking 'what's the weather in Paris?'"

            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": location,
                "appid": api_key,
                "units": "metric"
            }
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()

            temp = data["main"]["temp"]
            feels_like = data["main"]["feels_like"]
            description = data["weather"][0]["description"]
            humidity = data["main"]["humidity"]
            wind_speed = data["wind"]["speed"]

            return (
                f"Current weather in {location}: {description}, "
                f"{temp}°C (feels like {feels_like}°C), "
                f"humidity {humidity}%, wind {wind_speed} m/s"
            )

        except Exception as exc:
            log.error(f"OpenWeatherMap failed: {exc}")

    # Never hand back a plausible-sounding sentence when nothing was read.
    # The model must see that this is an absence of data, not data.
    return ToolFailure(
        "Weather lookup failed: no live reading was obtained"
        + (f" for {location}." if location else ".")
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CLOCK TOOL (already working, included for completeness)
# ═══════════════════════════════════════════════════════════════════════════════

def execute_clock_tool() -> str:
    """Get current date and time, synced to the Windows system clock."""
    tm = get_time_manager()
    now = tm.now()
    return (
        # One sentence, the time first: a plain lookup gets a one-sentence reply,
        # and "Today is ... . The time is ..." lost the time to that limit (S115).
        now.strftime("It's %I:%M %p on %A, %B %d, %Y").replace(" 0", " ", 1)
        + f" ({tm.get_timezone_name()})."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY TOOL
# ═══════════════════════════════════════════════════════════════════════════════

def execute_memory_tool(query: str, memory: dict | None = None) -> str:
    """
    Recall what MBUDDY actually knows that bears on this request.

    Searches the semantic vector store first, because it finds relevant
    material regardless of wording, then falls back to the stored fact lists
    so a recall still works before the embedding model has warmed up.

    Args:
        query: what the user said
        memory: the live memory dict, when the caller has one

    Returns:
        A short recall block, or an honest statement that nothing matched.
        Never a placeholder — an empty recall must read as empty.
    """
    lines: list[str] = []

    try:
        from memory.vector_store import get_vector_store
        hits = get_vector_store().search(query, top_k=4, load_if_needed=True)
        for hit in hits or []:
            text = str(hit.get("text") or hit.get("memory") or "").strip()
            if text:
                when = str(hit.get("time") or "")[:16]
                lines.append(f"- {text}" + (f"  ({when})" if when else ""))
    except Exception as exc:
        log.warn(f"Memory tool: vector search unavailable ({type(exc).__name__})")

    if not lines and memory:
        terms = {w for w in re.findall(r"\w{4,}", query.lower())}
        pool = list(memory.get("core_facts") or []) + list(memory.get("facts") or [])
        for fact in pool:
            text = str(fact).strip()
            if not text:
                continue
            if not terms or terms & set(re.findall(r"\w{4,}", text.lower())):
                lines.append(f"- {text}")
            if len(lines) >= 4:
                break

    if not lines:
        return "Nothing stored matches that. Say so plainly rather than inventing a memory."
    return "Recalled from memory:\n" + "\n".join(lines[:5])


# ═══════════════════════════════════════════════════════════════════════════════
# WEB SEARCH TOOL
# ═══════════════════════════════════════════════════════════════════════════════

def with_current_year(query: str) -> str:
    """
    "Who won the last Champions League final?" searched as-is returned the
    2025 final as often as the 2026 one; "last/latest/current" questions get
    the current year so the newest result ranks first.
    """
    if re.search(r"\b(last|latest|recent|current|newest|this year|now)\b", query, re.IGNORECASE) \
            and not re.search(r"\b(19|20)\d{2}\b", query):
        return f"{query} {datetime.now().year}"
    return query


def execute_web_search_tool(query: str) -> str:
    """
    Fetch genuinely current information from the live internet.

    This performs a real request every call. There is no cache and no
    fallback to model knowledge: if the lookup fails, it says so, so MBUDDY
    can tell the user it could not verify something rather than answering
    from stale training data.

    Args:
        query: what to look up

    Returns:
        The provider's result block stamped with when it was fetched, or a
        plain statement that the lookup did not succeed.
    """
    cleaned = re.sub(
        r'^\s*(please\s+)?(search|look\s*up|find|google)\s+(for\s+|me\s+)?',
        '', (query or '').strip(), flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        return "No search terms were given, so nothing was looked up."
    cleaned = with_current_year(cleaned)

    try:
        from utils.search_provider import get_search_provider
        provider = get_search_provider()
        result = provider.search(cleaned, max_results=3)
    except Exception as exc:
        log.error(f"Web search failed for '{cleaned}': {exc}")
        return (
            f"The live search for '{cleaned}' failed ({type(exc).__name__}). "
            "Tell USER_NAME you could not verify this right now. Do not answer from memory."
        )

    text = (result or "").strip()
    if not text or "no results" in text.lower():
        return (
            f"The live search for '{cleaned}' returned nothing usable. "
            "Say you could not confirm it rather than guessing."
        )

    stamp = get_time_manager().format_display()
    return f"Live search results for '{cleaned}' (fetched {stamp}):\n{text}"


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME TOOL
# ═══════════════════════════════════════════════════════════════════════════════

def plan_volume(query: str, current: int) -> tuple[str, int | None]:
    """
    What a volume request means: ("set", pct), ("mute_others", None),
    ("unmute_all", None) or ("report", current). Pure, so it is testable.
    """
    q = query.lower()
    if "unmute" in q:
        return ("unmute_all", None)
    if re.search(r"\bmute\b", q):
        return ("mute_others", None)
    number = re.search(r"(\d{1,3})\s*(?:%|percent)?", q)
    if number:
        return ("set", max(0, min(100, int(number.group(1)))))
    if re.search(r"\b(max|maximum|full|all the way up)\b", q):
        return ("set", 100)
    step = 20 if re.search(r"\b(a lot|way|much)\b", q) else 10
    # Complaints count as requests: "too loud" means down, "can't hear"
    # means up (live: "my head hurts, the volume is too loud" only reported).
    if re.search(r"\b(up|louder|raise|increase|higher)\b|too (quiet|low|soft)|can'?t hear|barely hear", q):
        return ("set", min(100, current + step))
    if re.search(r"\b(down|quieter|softer|lower|decrease|reduce)\b|(too|so|still|really|very|way) (loud|high)|\bloud\b", q):
        return ("set", max(0, current - step))
    return ("report", current)


def is_mbuddy_voice(proc) -> bool:
    """
    Whether an audio session belongs to MBUDDY's own voice: the desktop
    shell (mbuddy-shell.exe) or a process it started, such as the WebView2
    that plays replies. MBUDDY_VOICE_PROCESSES adds names (comma-separated),
    e.g. "chrome.exe" while the voice bridge runs in a Chrome tab.
    """
    if proc is None:
        return False
    import os
    names = {"mbuddy-shell.exe"} | {
        n.strip().lower() for n in os.getenv("MBUDDY_VOICE_PROCESSES", "").split(",") if n.strip()
    }
    try:
        chain = [proc] + list(proc.parents())
        return any(p.name().lower() in names for p in chain)
    except Exception:
        return False


def volume_plan_for_action(action: str, amount: float | None, current: int) -> tuple[str, int | None]:
    """plan_volume() for a structured request (the tool-calling model's)."""
    action = (action or "").lower()
    if action == "mute":
        return ("mute_others", None)
    if action == "unmute":
        return ("unmute_all", None)
    if action == "set" and amount is not None:
        return ("set", max(0, min(100, int(amount))))
    # The model picks big steps (live: 70 -> 40 -> 10 in two turns). Steps
    # are capped at 15 points, and "down" never goes below 5%.
    step = max(1, min(15, int(amount))) if amount else 10
    if action == "up":
        return ("set", min(100, current + step))
    if action == "down":
        return ("set", max(5, current - step))
    return ("report", current)


def _output_volume(audio):
    """The output device's volume control. "Element not found" means Windows
    had no default device for that role at that moment (e.g. mid-switch after
    sleep, U1b): ask again, then the other role's default, then the first
    active output device, before giving up."""
    try:
        return audio.GetSpeakers().EndpointVolume
    except Exception:
        pass
    time.sleep(0.3)
    try:
        return audio.GetSpeakers().EndpointVolume
    except Exception:
        pass
    try:
        from pycaw.utils import AudioDevice
        enumerator = audio.GetDeviceEnumerator()
        return AudioDevice("", "", None, enumerator.GetDefaultAudioEndpoint(0, 1)).EndpointVolume  # render, multimedia
    except Exception:
        pass
    for device in audio.GetAllDevices(data_flow=0, device_state=1):  # render, active
        try:
            return device.EndpointVolume
        except Exception:
            continue
    raise OSError("no active output device")


def _run_volume(decide) -> str:
    """
    Apply a volume decision via the Core Audio API (pycaw).

    `decide(current_percent)` returns a plan_volume()-style tuple. "Mute"
    mutes every other app's audio session and keeps the master volume on,
    so MBUDDY can still be heard; "unmute" undoes both. COM is initialised
    per call because tools run on worker threads, where it was not
    ("CoInitialize has not been called" made "turn up the volume" fail).
    """
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
    except ImportError as exc:
        log.error(f"Volume tool: pycaw not available ({exc})")
        return ToolFailure("Volume control isn't available on this system right now.")

    comtypes.CoInitialize()
    try:
        try:
            volume = _output_volume(AudioUtilities)
            _run_volume._endpoint_down = False
        except Exception as exc:
            # Once per outage, not on every poll (live: logged every few
            # seconds while Windows had no output device, S90).
            if not getattr(_run_volume, "_endpoint_down", False):
                log.error(f"Volume tool: could not reach the audio endpoint ({exc})")
            _run_volume._endpoint_down = True
            if "no active output device" in str(exc):
                return ToolFailure("Windows has no active speakers or headphones right now, so there's nothing "
                                   "to set the volume on. Plugging a device in, or restarting the audio driver, "
                                   "usually brings them back.")
            return ToolFailure("I couldn't reach the system audio device to change the volume.")

        current = round(volume.GetMasterVolumeLevelScalar() * 100)
        action, value = decide(current)

        if action == "set":
            volume.SetMasterVolumeLevelScalar(value / 100.0, None)
            volume.SetMute(0, None)
            return f"System volume set to {value}% (was {current}%)."
        if action in ("mute_others", "unmute_all"):
            mute = action == "mute_others"
            volume.SetMute(0, None)  # master stays on either way, or MBUDDY is silent too
            changed = []
            for session in AudioUtilities.GetAllSessions():
                if mute and is_mbuddy_voice(session.Process):
                    continue
                session.SimpleAudioVolume.SetMute(1 if mute else 0, None)
                if session.Process is not None:
                    changed.append(session.Process.name())
            apps = ", ".join(sorted(set(changed))) or "nothing else was playing"
            return (f"Muted other apps ({apps}); MBUDDY's voice stays on." if mute
                    else f"Unmuted all audio ({apps}).")
        return f"Current system volume is {current}%."
    finally:
        from brain.com_guard import end as _com_end; _com_end()


def execute_volume_tool(query: str) -> str:
    """Volume from a spoken request ("turn it down", "mute", "set to 30")."""
    return _run_volume(lambda current: plan_volume(query, current))


def apply_volume_action(action: str, amount: float | None = None) -> str:
    """Volume from a structured request: action up/down/set/mute/unmute."""
    return _run_volume(lambda current: volume_plan_for_action(action, amount, current))


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

def execute_tool(tool_name: str, query: str = "", **kwargs) -> str:
    """Route tool execution to the appropriate handler."""

    try:
        if tool_name == "clock":
            return execute_clock_tool()

        elif tool_name == "timer":
            return execute_timer_tool(query)

        elif tool_name == "task":
            return execute_task_tool(query)

        elif tool_name == "calculator":
            return execute_calculator_tool(query)

        elif tool_name == "memory":
            return execute_memory_tool(query, kwargs.get("memory"))

        elif tool_name == "web_search":
            return execute_web_search_tool(query)

        elif tool_name == "weather":
            location = kwargs.get("location", "") or extract_location(query)
            return execute_weather_tool(location)

        elif tool_name == "sys_info":
            import platform, psutil
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            return f"System Info: OS {platform.system()} {platform.release()}, CPU Usage {cpu}%, RAM Usage {ram}%."

        elif tool_name == "sys_volume":
            return execute_volume_tool(query)

        elif tool_name == "sys_app_launch":
            import subprocess
            app_match = re.search(r'(notepad|calc|chrome|explorer|edge|cmd)', query, re.IGNORECASE)
            if app_match:
                app_name = app_match.group(1).lower()
                from core.launch import open_for_user
                open_for_user(app_name)  # outside MBUDDY's job (U1)
                return f"Launched {app_name} successfully."
            return "Please specify an application to launch (e.g. notepad, calc, chrome)."

        else:
            return f"Tool '{tool_name}' is not implemented."

    except Exception as exc:
        log.error(f"Tool execution failed for {tool_name}: {exc}")
        return f"Sorry, the {tool_name} tool encountered an error."


# ═══════════════════════════════════════════════════════════════════════════════
# ASYNC WRAPPER for use in async contexts
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_tool_async(tool_name: str, query: str = "", **kwargs) -> str:
    """Async wrapper for tool execution (runs in thread executor)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: execute_tool(tool_name, query, **kwargs))
