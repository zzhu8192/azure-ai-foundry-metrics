"""Unit tests for time window boundary calculations."""

from datetime import datetime, timezone
from foundry_metrics.time_windows import calculate_windows


def test_calculate_windows_utc():
    # Wednesday, 2026-09-09 15:30:00 UTC
    ref = datetime(2026, 9, 9, 15, 30, 0, tzinfo=timezone.utc)
    windows = calculate_windows(tz_name="utc", reference_dt=ref)

    # Today starts at 2026-09-09 00:00:00
    today = windows["today"]
    assert today.start == datetime(2026, 9, 9, 0, 0, 0, tzinfo=timezone.utc)
    assert today.end == ref

    # Week starts Monday, 2026-09-07 00:00:00
    week = windows["week"]
    assert week.start == datetime(2026, 9, 7, 0, 0, 0, tzinfo=timezone.utc)
    assert week.end == ref

    # Month starts 1st, 2026-09-01 00:00:00
    month = windows["month"]
    assert month.start == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert month.end == ref


def test_azure_timespan_format():
    ref = datetime(2026, 9, 9, 15, 30, 0, tzinfo=timezone.utc)
    windows = calculate_windows(tz_name="utc", reference_dt=ref)
    today = windows["today"]

    assert today.azure_timespan == "2026-09-09T00:00:00Z/2026-09-09T15:30:00Z"
    assert today.cost_management_period["from"] == "2026-09-09T00:00:00Z"
    assert today.cost_management_period["to"] == "2026-09-09T23:59:59Z"
