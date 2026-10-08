from datetime import time

import pytest

from tv_mgmt_pure.quiet import QuietWindow, active_window, parse_window, parse_windows


def test_parse_with_label():
    w = parse_window("20:30-07:00 Bedtime")
    assert w == QuietWindow(time(20, 30), time(7, 0), "Bedtime")
    assert w.format() == "20:30-07:00 Bedtime"


def test_parse_list_and_blank():
    assert parse_windows("") == []
    assert parse_windows(None) == []
    windows = parse_windows("12:00-13:00 Lunch, 20:30-07:00")
    assert [w.name for w in windows] == ["Lunch", "20:30-07:00"]


@pytest.mark.parametrize("raw", ["20:30", "25:00-07:00", "8-9", "20:30-07:61", "aa:bb-cc:dd"])
def test_parse_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_window(raw)


def test_same_day_window():
    w = parse_window("12:00-13:00")
    assert w.contains(time(12, 0))
    assert w.contains(time(12, 59))
    assert not w.contains(time(13, 0))
    assert not w.contains(time(11, 59))


def test_crosses_midnight():
    w = parse_window("20:30-07:00")
    assert w.contains(time(23, 0))
    assert w.contains(time(0, 0))
    assert w.contains(time(6, 59))
    assert not w.contains(time(7, 0))
    assert not w.contains(time(20, 29))


def test_empty_window_never_matches():
    assert not parse_window("08:00-08:00").contains(time(8, 0))


def test_active_window_picks_first_match():
    windows = parse_windows("12:00-13:00 Lunch, 20:30-07:00 Bedtime")
    assert active_window(windows, time(12, 30)).label == "Lunch"
    assert active_window(windows, time(22, 0)).label == "Bedtime"
    assert active_window(windows, time(16, 0)) is None
