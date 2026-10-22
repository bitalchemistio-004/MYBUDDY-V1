"""
core/time_manager.py — Windows Platform Time Management

Provides Windows-synchronized time for the entire MBUDDY system.
All components should use this module for consistent, timezone-aware time operations.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import json
import time
import ctypes
from ctypes import wintypes

# Windows API for time zone info
TIME_ZONE_ID_INVALID = 0xFFFFFFFF

# A user-set clock offset is persisted here so the backend and the desktop
# app agree on what time MBUDDY thinks it is across restarts.
TIME_CONFIG_PATH = Path.home() / ".buddy" / "config" / "time_settings.json"

class TIME_ZONE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('Bias', wintypes.LONG),
        ('StandardName', wintypes.WCHAR * 32),
        ('StandardDate', wintypes.BYTE * 16),
        ('StandardBias', wintypes.LONG),
        ('DaylightName', wintypes.WCHAR * 32),
        ('DaylightDate', wintypes.BYTE * 16),
        ('DaylightBias', wintypes.LONG),
    ]


class WindowsTimeManager:
    """
    Manages system time synchronized with Windows platform.

    **What it does:**
    - Provides consistent timezone-aware timestamps
    - Syncs with Windows system time
    - Formats times for display and logging
    - Tracks uptime and session duration

    **How to use:**
    ```python
    from core.time_manager import get_time_manager

    tm = get_time_manager()

    # Get current time
    now = tm.now()  # datetime with local timezone

    # Get UTC time
    utc = tm.utc_now()

    # Format for display
    display = tm.format_display()  # "Sunday, September 1, 2026 at 13:03"

    # Format for logs
    log = tm.format_log()  # "2026-09-01 13:03:22.952"

    # Get uptime
    uptime = tm.get_uptime_seconds()
    ```
    """

    def __init__(self):
        self._start_time = time.time()
        self._timezone_name: Optional[str] = None
        self._timezone_offset: Optional[int] = None
        # Minutes to add to the Windows clock. Lets the user correct a wrong
        # system clock, or shift MBUDDY's sense of time to test how it behaves
        # late at night without waiting for it to actually be late at night.
        self._offset_minutes: float = 0.0
        self._update_timezone_info()
        self._load_offset()

    # ── User clock offset ─────────────────────────────────────────────
    def _load_offset(self) -> None:
        """Restore a previously saved clock offset."""
        try:
            if TIME_CONFIG_PATH.exists():
                data = json.loads(TIME_CONFIG_PATH.read_text(encoding="utf-8"))
                self._offset_minutes = float(data.get("offset_minutes", 0.0))
        except Exception:
            self._offset_minutes = 0.0

    def _save_offset(self) -> None:
        try:
            TIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            TIME_CONFIG_PATH.write_text(
                json.dumps({"offset_minutes": self._offset_minutes}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def set_offset_minutes(self, minutes: float) -> None:
        """
        Shift MBUDDY's clock relative to the Windows system clock.

        Args:
            minutes: how far ahead (positive) or behind (negative) to run

        Everything that asks this manager for the time sees the shift, so
        MBUDDY's greetings, energy curve, and timestamps all move together.
        """
        self._offset_minutes = float(minutes)
        self._save_offset()

    def set_time(self, target: datetime) -> float:
        """
        Make MBUDDY believe it is `target` right now.

        This does not change the Windows clock — it stores the difference as
        an offset, so the clock keeps running normally from that point.

        Args:
            target: the local time MBUDDY should report

        Returns:
            float: the resulting offset in minutes
        """
        if target.tzinfo is not None:
            target = target.replace(tzinfo=None)
        delta = (target - datetime.now()).total_seconds() / 60.0
        self.set_offset_minutes(delta)
        return self._offset_minutes

    def clear_offset(self) -> None:
        """Go back to following the Windows clock exactly."""
        self.set_offset_minutes(0.0)

    def get_offset_minutes(self) -> float:
        """Current offset from the Windows clock, in minutes."""
        return self._offset_minutes

    def is_offset(self) -> bool:
        """Whether MBUDDY's clock is deliberately running off the system one."""
        return abs(self._offset_minutes) >= 0.5

    def _update_timezone_info(self) -> None:
        """Get Windows timezone information via API."""
        try:
            tz_info = TIME_ZONE_INFORMATION()
            result = ctypes.windll.kernel32.GetTimeZoneInformation(ctypes.byref(tz_info))

            if result != TIME_ZONE_ID_INVALID:
                # Store timezone name and offset
                if result == 2:  # Daylight time
                    self._timezone_name = tz_info.DaylightName
                    self._timezone_offset = -(tz_info.Bias + tz_info.DaylightBias)
                else:  # Standard time
                    self._timezone_name = tz_info.StandardName
                    self._timezone_offset = -tz_info.Bias
        except Exception:
            # Fallback to Python's timezone detection
            self._timezone_name = time.tzname[time.daylight]
            self._timezone_offset = -time.timezone // 60 if not time.daylight else -time.altzone // 60

    def now(self) -> datetime:
        """
        Get current local time with timezone, including any user offset.

        Returns:
            datetime: Current local time (timezone-aware)
        """
        base = datetime.now().astimezone()
        if self._offset_minutes:
            return base + timedelta(minutes=self._offset_minutes)
        return base

    def naive_now(self) -> datetime:
        """
        Get current local time without a timezone attached.

        Use this anywhere the result is compared against, or stored
        alongside, timestamps that were written without a timezone —
        task due dates and memory entries, for example. Mixing aware and
        naive datetimes raises TypeError on comparison, so those call
        sites need a consistently naive clock rather than `now()`.

        Returns:
            datetime: Current local time (no tzinfo)
        """
        base = datetime.now()
        if self._offset_minutes:
            return base + timedelta(minutes=self._offset_minutes)
        return base

    def utc_now(self) -> datetime:
        """
        Get current UTC time.

        Returns:
            datetime: Current UTC time (timezone-aware)
        """
        return datetime.now(timezone.utc)

    def format_display(self, dt: Optional[datetime] = None) -> str:
        """
        Format time for user-facing display.

        Args:
            dt: Optional datetime to format (defaults to now)

        Returns:
            str: Formatted time like "Sunday, September 1, 2026 at 13:03"
        """
        if dt is None:
            dt = self.now()
        return dt.strftime("%A, %B %d, %Y at %H:%M")

    def format_log(self, dt: Optional[datetime] = None) -> str:
        """
        Format time for logging.

        Args:
            dt: Optional datetime to format (defaults to now)

        Returns:
            str: Formatted time like "2026-09-01 13:03:22.952"
        """
        if dt is None:
            dt = self.now()
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Milliseconds

    def format_short(self, dt: Optional[datetime] = None) -> str:
        """
        Format time for compact display.

        Args:
            dt: Optional datetime to format (defaults to now)

        Returns:
            str: Formatted time like "13:03:22"
        """
        if dt is None:
            dt = self.now()
        return dt.strftime("%H:%M:%S")

    def format_date(self, dt: Optional[datetime] = None) -> str:
        """
        Format date only.

        Args:
            dt: Optional datetime to format (defaults to now)

        Returns:
            str: Formatted date like "September 1, 2026"
        """
        if dt is None:
            dt = self.now()
        return dt.strftime("%B %d, %Y")

    def get_day_period(self, dt: Optional[datetime] = None) -> str:
        """
        Get time of day period.

        Args:
            dt: Optional datetime (defaults to now)

        Returns:
            str: "morning", "afternoon", "evening", or "night"
        """
        if dt is None:
            dt = self.now()

        hour = dt.hour

        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "night"

    def get_timezone_name(self) -> str:
        """
        Get Windows timezone name.

        Returns:
            str: Timezone name like "Eastern Standard Time"
        """
        if self._timezone_name:
            return self._timezone_name
        return time.tzname[time.daylight]

    def get_timezone_offset(self) -> int:
        """
        Get timezone offset in minutes.

        Returns:
            int: Offset from UTC in minutes (e.g., -300 for EST)
        """
        if self._timezone_offset is not None:
            return self._timezone_offset
        return -time.timezone // 60

    def get_uptime_seconds(self) -> float:
        """
        Get MBUDDY uptime since initialization.

        Returns:
            float: Seconds since start
        """
        return time.time() - self._start_time

    def get_uptime_display(self) -> str:
        """
        Get formatted uptime string.

        Returns:
            str: Formatted uptime like "2h 15m 30s"
        """
        seconds = int(self.get_uptime_seconds())

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    def to_iso(self, dt: Optional[datetime] = None) -> str:
        """
        Convert to ISO 8601 format.

        Args:
            dt: Optional datetime (defaults to now)

        Returns:
            str: ISO formatted time like "2026-09-01T13:03:22.952+03:00"
        """
        if dt is None:
            dt = self.now()
        return dt.isoformat()

    def from_iso(self, iso_string: str) -> datetime:
        """
        Parse ISO 8601 formatted string.

        Args:
            iso_string: ISO formatted datetime string

        Returns:
            datetime: Parsed datetime object
        """
        return datetime.fromisoformat(iso_string)

    def timestamp(self) -> float:
        """
        Get current Unix timestamp.

        Returns:
            float: Seconds since epoch
        """
        return time.time()

    def from_timestamp(self, ts: float) -> datetime:
        """
        Convert Unix timestamp to local datetime.

        Args:
            ts: Unix timestamp

        Returns:
            datetime: Local datetime
        """
        return datetime.fromtimestamp(ts).astimezone()


# Global singleton instance
_time_manager: Optional[WindowsTimeManager] = None


def get_time_manager() -> WindowsTimeManager:
    """
    Get the global WindowsTimeManager instance.

    Returns:
        WindowsTimeManager: Singleton time manager
    """
    global _time_manager
    if _time_manager is None:
        _time_manager = WindowsTimeManager()
    return _time_manager


def now() -> datetime:
    """
    Convenience function to get current local time.

    Returns:
        datetime: Current local time
    """
    return get_time_manager().now()


def utc_now() -> datetime:
    """
    Convenience function to get current UTC time.

    Returns:
        datetime: Current UTC time
    """
    return get_time_manager().utc_now()


def format_display(dt: Optional[datetime] = None) -> str:
    """
    Convenience function to format time for display.

    Args:
        dt: Optional datetime (defaults to now)

    Returns:
        str: Formatted time
    """
    return get_time_manager().format_display(dt)


def format_log(dt: Optional[datetime] = None) -> str:
    """
    Convenience function to format time for logging.

    Args:
        dt: Optional datetime (defaults to now)

    Returns:
        str: Formatted time for logs
    """
    return get_time_manager().format_log(dt)


# Initialize on import
get_time_manager()
