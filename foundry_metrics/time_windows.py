"""Time window calculation for Today, Current Week, and Current Month."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Dict, Optional
import zoneinfo


@dataclass
class WindowBounds:
    """Start and end bounds for a monitoring time window."""
    name: str  # "Today", "Current Week", "Current Month"
    start: datetime
    end: datetime
    timezone_name: str

    @property
    def start_utc(self) -> datetime:
        return self.start.astimezone(timezone.utc)

    @property
    def end_utc(self) -> datetime:
        return self.end.astimezone(timezone.utc)

    @property
    def azure_timespan(self) -> str:
        """ISO 8601 timespan format required by Azure Monitor Metrics API."""
        s = self.start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        e = self.end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{s}/{e}"

    @property
    def cost_management_period(self) -> Dict[str, str]:
        """TimePeriod object for Azure Cost Management API."""
        return {
            "from": self.start_utc.strftime("%Y-%m-%dT00:00:00Z"),
            "to": self.end_utc.strftime("%Y-%m-%dT23:59:59Z"),
        }


def get_timezone(tz_name: str = "local") -> datetime.tzinfo:
    """Resolve timezone object."""
    if tz_name.lower() == "utc":
        return timezone.utc
    try:
        # Try local system timezone
        import time as _time
        local_tz_name = _time.tzname[0]
        # Use Python's standard local tz
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception:
        return timezone.utc


def calculate_windows(
    tz_name: str = "local",
    reference_dt: Optional[datetime] = None,
) -> Dict[str, WindowBounds]:
    """Calculate the exact bounds for Today, Current Week, and Current Month.

    Args:
        tz_name: "local" or "utc" (or any IANA tz name like "America/Chicago")
        reference_dt: Optional reference datetime for testing/mocking.

    Returns:
        Dict mapping "today", "week", "month" to WindowBounds.
    """
    if tz_name.lower() == "utc":
        tz = timezone.utc
        display_tz = "UTC"
    else:
        tz = get_timezone(tz_name)
        display_tz = str(tz)

    now = reference_dt or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    # 1. Today: Start of today (00:00:00) to now
    today_start = datetime.combine(now.date(), time.min, tzinfo=tz)

    # 2. Current Week: Monday of current week (00:00:00) to now
    # now.weekday(): 0 = Monday, 6 = Sunday
    days_since_monday = now.weekday()
    week_start_date = now.date() - timedelta(days=days_since_monday)
    week_start = datetime.combine(week_start_date, time.min, tzinfo=tz)

    # 3. Current Month: 1st day of current month (00:00:00) to now
    month_start_date = now.date().replace(day=1)
    month_start = datetime.combine(month_start_date, time.min, tzinfo=tz)

    return {
        "today": WindowBounds(
            name="Today",
            start=today_start,
            end=now,
            timezone_name=display_tz,
        ),
        "week": WindowBounds(
            name="Current Week",
            start=week_start,
            end=now,
            timezone_name=display_tz,
        ),
        "month": WindowBounds(
            name="Current Month",
            start=month_start,
            end=now,
            timezone_name=display_tz,
        ),
    }
