from datetime import date, datetime, timedelta, timezone

from tv_mgmt_pure.activity import (
    EV_INPUT,
    EV_INPUT_BLOCKED,
    EV_TV_OFF,
    EV_TV_ON,
    KEEP_EVENT_DAYS,
    ActivityLog,
    summarize,
)

UTC = timezone.utc


def at(h, m=0, day=8):
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def test_segments_split_by_input_and_power():
    log = ActivityLog()
    log.add(at(15), EV_TV_ON, source="HDMI 2")
    log.add(at(15, 30), EV_INPUT, source="YouTube")
    log.add(at(15, 31), EV_INPUT, source="HDMI 2")
    log.add(at(16), EV_TV_OFF)
    segs = log.viewing_segments(at(0), at(0, day=9), now=at(20))
    assert [(s["source"], s["seconds"]) for s in segs] == [
        ("HDMI 2", 1800), ("YouTube", 60), ("HDMI 2", 1740)
    ]
    assert not any(s["live"] for s in segs)


def test_live_segment_and_clipping_to_day():
    log = ActivityLog()
    log.add(at(23, day=7), EV_TV_ON, source="HDMI 2")  # started the day before
    segs = log.viewing_segments(at(0), at(0, day=9), now=at(1))
    assert len(segs) == 1
    assert segs[0]["start"] == at(0).isoformat()
    assert segs[0]["seconds"] == 3600
    assert segs[0]["live"] is True


def test_events_between_and_out_of_order_insert():
    log = ActivityLog()
    log.add(at(10), EV_TV_ON, source="HDMI 2")
    log.add(at(12), EV_TV_OFF)
    log.add(at(11), EV_INPUT_BLOCKED, source="YouTube", target="HDMI 2", reverted=True)
    assert [e["type"] for e in log.events] == [EV_TV_ON, EV_INPUT_BLOCKED, EV_TV_OFF]
    assert len(log.events_between(at(10, 30), at(13))) == 2


def test_last_power():
    log = ActivityLog()
    assert log.last_power == (None, None)
    log.add(at(10), EV_TV_ON, source="HDMI 1")
    log.add(at(11), EV_INPUT, source="HDMI 2")
    assert log.last_power == (True, "HDMI 2")
    log.add(at(12), EV_TV_OFF)
    assert log.last_power == (False, None)


def test_prune_keeps_state_before_cutoff():
    log = ActivityLog()
    old = at(10) - timedelta(days=KEEP_EVENT_DAYS + 5)
    log.add(old, EV_TV_ON, source="HDMI 2")
    log.add(old + timedelta(hours=1), EV_INPUT_BLOCKED, source="YouTube", target="HDMI 2", reverted=True)
    log.add(at(10), EV_TV_OFF)
    log.daily["2020-01-01"] = {"used_seconds": 1, "budget_minutes": 0, "extension_minutes": 0, "blocked": 0}
    log.prune(at(12))
    assert [e["type"] for e in log.events] == [EV_TV_ON, EV_TV_OFF]
    assert "2020-01-01" not in log.daily


def test_record_day_reports_changes():
    log = ActivityLog()
    assert log.record_day(date(2026, 10, 8), used_seconds=60, budget_minutes=90, extension_minutes=0, blocked=1)
    assert not log.record_day("2026-10-08", used_seconds=60, budget_minutes=90, extension_minutes=0, blocked=1)


def test_daily_series_and_summary():
    log = ActivityLog()
    log.record_day("2026-10-06", used_seconds=100 * 60, budget_minutes=90, extension_minutes=0, blocked=2)
    log.record_day("2026-10-08", used_seconds=30 * 60, budget_minutes=90, extension_minutes=30, blocked=1)
    series = log.daily_series(date(2026, 10, 8), 3)
    assert [d["date"] for d in series] == ["2026-10-06", "2026-10-07", "2026-10-08"]
    assert series[1]["recorded"] is False
    summary = summarize(series)
    assert summary["total_seconds"] == 130 * 60
    assert summary["days_over_limit"] == 1
    assert summary["blocked"] == 3
    assert summary["active_days"] == 2
    assert summary["average_seconds"] == 65 * 60


def test_round_trip():
    log = ActivityLog()
    log.add(at(10), EV_TV_ON, source="HDMI 2")
    log.record_day("2026-10-08", used_seconds=1, budget_minutes=0, extension_minutes=0, blocked=0)
    again = ActivityLog(log.as_dict())
    assert again.events == log.events and again.daily == log.daily
    assert again.first_day == "2026-10-08"
