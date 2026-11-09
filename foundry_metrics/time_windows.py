"""Time window calculation for Today, Current Week, and Current Month."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Dict, Optional
import zoneinfo


@dataclass
class WindowBounds:
    """Start and end bounds for a monitoring time window."""
    name: str
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
            "to": (self.end_utc - timedelta(microseconds=1)).strftime("%Y-%m-%dT23:59:59Z"),
        }


def get_timezone(tz_name: str = "local") -> tzinfo:
    """Resolve timezone object."""
    if tz_name.lower() == "utc":
        return timezone.utc
    if tz_name.lower() == "local":
        from tzlocal import get_localzone
        return get_localzone()
    return zoneinfo.ZoneInfo(tz_name)


def parse_datetime(value: str, tz: tzinfo) -> datetime:
    """Parse an ISO date/time; a date alone means midnight in the chosen zone."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)


def _split_into_daily_windows(
    start_dt: datetime,
    end_dt: datetime,
    tz: tzinfo,
    display_tz: str,
) -> Dict[str, WindowBounds]:
    """Split a continuous time range into individual calendar day WindowBounds."""
    windows: Dict[str, WindowBounds] = {}
    if start_dt.astimezone(timezone.utc) >= end_dt.astimezone(timezone.utc):
        return windows

    last_date = (end_dt - timedelta(microseconds=1)).date()
    cur_date = start_dt.date()

    while cur_date <= last_date:
        day_start = max(start_dt, datetime.combine(cur_date, time.min, tzinfo=tz))
        next_day_start = datetime.combine(cur_date + timedelta(days=1), time.min, tzinfo=tz)
        day_end = min(end_dt, next_day_start)

        if day_start < day_end:
            day_str = cur_date.strftime("%Y-%m-%d")
            windows[day_str] = WindowBounds(
                name=day_str,
                start=day_start,
                end=day_end,
                timezone_name=display_tz,
            )
        cur_date += timedelta(days=1)

    return windows


def calculate_windows(
    tz_name: str = "local",
    reference_dt: Optional[datetime] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    daily: bool = False,
    period: str = "all",
) -> Dict[str, WindowBounds]:
    """Calculate the exact bounds for monitoring time windows.

    Args:
        tz_name: "local" or "utc" (or any IANA tz name like "America/Chicago")
        reference_dt: Optional reference datetime for testing/mocking.
        start: Optional start ISO datetime for custom range.
        end: Optional end ISO datetime for custom range.
        daily: If True, break out the selected period/range into individual daily windows.
        period: Time period name ("all", "today", "3d", "week", "month", "7d").

    Returns:
        Dict mapping period names/dates to WindowBounds.
    """
    tz = get_timezone(tz_name)
    display_tz = str(tz)

    now = reference_dt or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    if start is not None:
        custom_start = parse_datetime(start, tz)
        custom_end = parse_datetime(end, tz) if end is not None else now
        if custom_start.astimezone(timezone.utc) >= custom_end.astimezone(timezone.utc):
            raise ValueError("--start must precede --end (the end is exclusive).")
        if daily:
            return _split_into_daily_windows(custom_start, custom_end, tz, display_tz)
        return {"custom": WindowBounds("Custom", custom_start, custom_end, display_tz)}
    if end is not None:
        raise ValueError("--end requires --start.")

    if daily:
        target_period = "7d" if period.lower() == "all" else period.lower()
        if target_period == "3d":
            p_start = datetime.combine(now.date() - timedelta(days=2), time.min, tzinfo=tz)
        elif target_period == "7d":
            p_start = datetime.combine(now.date() - timedelta(days=6), time.min, tzinfo=tz)
        elif target_period == "week":
            p_start = datetime.combine(now.date() - timedelta(days=now.weekday()), time.min, tzinfo=tz)
        elif target_period == "month":
            p_start = datetime.combine(now.date().replace(day=1), time.min, tzinfo=tz)
        elif target_period == "today":
            p_start = datetime.combine(now.date(), time.min, tzinfo=tz)
        else:
            p_start = datetime.combine(now.date() - timedelta(days=6), time.min, tzinfo=tz)
        return _split_into_daily_windows(p_start, now, tz, display_tz)

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
        "3d": WindowBounds(
            name="Last 3 Days",
            start=(now.astimezone(timezone.utc) - timedelta(days=3)).astimezone(tz),
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
        # Subtract in UTC so 7D is exactly 168 hours even across a DST change.
        "7d": WindowBounds(
            name="Last 7 Days",
            start=(now.astimezone(timezone.utc) - timedelta(days=7)).astimezone(tz),
            end=now,
            timezone_name=display_tz,
        ),
    }
