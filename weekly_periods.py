"""Date-math helpers for the weekly digest.

All returns are ET-tz-aware datetimes. Uses fixed UTC-4 offset (EDT) — acceptable
for weekly reporting where DST boundary lands on a Mon 11:00 UTC cron with minimal
drift. If precision matters later, swap to zoneinfo('America/New_York').
"""
import datetime

ET = datetime.timezone(datetime.timedelta(hours=-4))  # EDT; daily digest uses same


def _start_of_day(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def last_week_range(now_et: datetime.datetime) -> tuple:
    """Most-recently-completed Mon 00:00 — Sun 23:59:59 in ET."""
    days_since_monday = now_et.weekday()
    this_monday = _start_of_day(now_et - datetime.timedelta(days=days_since_monday))
    last_monday = this_monday - datetime.timedelta(days=7)
    last_sunday = _end_of_day(last_monday + datetime.timedelta(days=6))
    return last_monday, last_sunday


def mtd_range(now_et: datetime.datetime) -> tuple:
    """First-of-month 00:00 to now."""
    start = _start_of_day(now_et.replace(day=1))
    return start, now_et


def prior_week_range(now_et: datetime.datetime) -> tuple:
    """The week before last_week_range — for WoW comparison."""
    last_start, _ = last_week_range(now_et)
    prior_start = last_start - datetime.timedelta(days=7)
    prior_end = _end_of_day(prior_start + datetime.timedelta(days=6))
    return prior_start, prior_end
