import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from weekly_periods import last_week_range, mtd_range, prior_week_range, ET


def test_last_week_range_from_monday():
    # Monday 2026-04-13 10:00 ET → last week is Mon 04-06 to Sun 04-12
    now = datetime.datetime(2026, 4, 13, 10, 0, tzinfo=ET)
    start, end = last_week_range(now)
    assert start == datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=ET)
    assert end == datetime.datetime(2026, 4, 12, 23, 59, 59, tzinfo=ET)


def test_last_week_range_from_wednesday():
    # Wed 2026-04-15 → last week is still Mon 04-06 to Sun 04-12
    now = datetime.datetime(2026, 4, 15, 10, 0, tzinfo=ET)
    start, end = last_week_range(now)
    assert start.day == 6
    assert end.day == 12


def test_last_week_range_from_sunday():
    # Sun 2026-04-12 → last week is Mon 03-30 to Sun 04-05
    now = datetime.datetime(2026, 4, 12, 10, 0, tzinfo=ET)
    start, end = last_week_range(now)
    assert start == datetime.datetime(2026, 3, 30, 0, 0, 0, tzinfo=ET)
    assert end == datetime.datetime(2026, 4, 5, 23, 59, 59, tzinfo=ET)


def test_mtd_range_mid_month():
    now = datetime.datetime(2026, 4, 14, 10, 0, tzinfo=ET)
    start, end = mtd_range(now)
    assert start == datetime.datetime(2026, 4, 1, 0, 0, 0, tzinfo=ET)
    assert end == now


def test_mtd_range_first_of_month():
    now = datetime.datetime(2026, 4, 1, 10, 0, tzinfo=ET)
    start, end = mtd_range(now)
    assert start == datetime.datetime(2026, 4, 1, 0, 0, 0, tzinfo=ET)
    assert end == now


def test_prior_week_range_from_monday():
    # Monday 04-13 → prior week is Mon 03-30 to Sun 04-05
    now = datetime.datetime(2026, 4, 13, 10, 0, tzinfo=ET)
    start, end = prior_week_range(now)
    assert start == datetime.datetime(2026, 3, 30, 0, 0, 0, tzinfo=ET)
    assert end == datetime.datetime(2026, 4, 5, 23, 59, 59, tzinfo=ET)
