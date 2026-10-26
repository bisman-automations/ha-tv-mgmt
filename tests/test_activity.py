from datetime import date, datetime, timedelta, timezone

from tv_mgmt_pure.activity import (
    ADULT_EXPIRED,
    EV_ADULT_MODE,
    EV_APP,
    EV_INPUT,
    EV_INPUT_BLOCKED,
    EV_MEDIA,
    EV_TV_OFF,
    EV_TV_ON,
    KEEP_EVENT_DAYS,
    ActivityLog,
    hide_adult_detail,
    overlap_seconds,
    subtract_periods,
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


def test_app_segments():
    from tv_mgmt_pure.activity import EV_APP

    log = ActivityLog()
    log.add(at(15), EV_APP, app="com.google.ios.youtube", name="YouTube")
    log.add(at(15, 20), EV_APP, app=None, name=None)  # home screen
    log.add(at(15, 25), EV_APP, app="com.disney.disneyplus", name="Disney+")
    segs = log.app_segments(at(0), at(0, day=9), now=at(16))
    assert [(s["name"], s["seconds"]) for s in segs] == [("YouTube", 1200), ("Disney+", 2100)]
    assert segs[-1]["live"] is True


def test_daily_apps_and_top_apps():
    log = ActivityLog()
    log.record_day("2026-10-07", used_seconds=60, budget_minutes=0, extension_minutes=0, blocked=0,
                   apps={"yt": 600, "disney": 300}, apps_stopped=2)
    log.record_day("2026-10-08", used_seconds=60, budget_minutes=0, extension_minutes=0, blocked=0,
                   apps={"disney": 900})
    summary = summarize(log.daily_series(date(2026, 10, 8), 2))
    assert summary["top_apps"] == [{"app": "disney", "seconds": 1200}, {"app": "yt", "seconds": 600}]
    assert summary["apps_stopped"] == 2


# ---- adult mode (1.16.0) -------------------------------------------------------------

def _periods(log, now, day=8):
    return [(p["start"][11:16], p["end"][11:16], p["live"])
            for p in log.adult_periods(at(0, day=day), at(0, day=day + 1), now)]


def test_adult_periods_from_on_and_off():
    log = ActivityLog()
    log.add(at(20), EV_ADULT_MODE, on=True, minutes=120)
    log.add(at(20, 45), EV_ADULT_MODE, on=False)
    log.add(at(21), EV_ADULT_MODE, on=True, minutes=30)
    log.add(at(21, 30), EV_ADULT_MODE, on=False, reason=ADULT_EXPIRED)
    assert _periods(log, at(23)) == [("20:00", "20:45", False), ("21:00", "21:30", False)]


def test_adult_period_turned_on_again_extends_it():
    log = ActivityLog()
    log.add(at(20), EV_ADULT_MODE, on=True, minutes=30)
    log.add(at(20, 20), EV_ADULT_MODE, on=True, minutes=30)
    assert _periods(log, at(23)) == [("20:00", "20:50", False)]


def test_adult_period_before_expiry_was_logged():
    """Before 1.16.0 running out wasn't logged: it ends `minutes` after it was turned on."""
    log = ActivityLog()
    log.add(at(20), EV_ADULT_MODE, on=True, minutes=60)
    log.add(at(22), EV_TV_OFF)
    assert _periods(log, at(23)) == [("20:00", "21:00", False)]


def test_adult_period_live_and_clipped_to_the_day():
    log = ActivityLog()
    log.add(at(23), EV_ADULT_MODE, on=True, minutes=120)
    assert _periods(log, at(23, 30)) == [("23:00", "23:30", True)]
    # Seen the next day, after it ran out: the part after midnight.
    assert _periods(log, at(3, day=9), day=9) == [("00:00", "01:00", False)]


def test_hide_adult_detail_keeps_everything_else():
    log = ActivityLog()
    log.add(at(20), EV_APP, app="a", name="A")
    log.add(at(20, 10), EV_ADULT_MODE, on=True, minutes=60)
    log.add(at(20, 10), EV_APP, app="b", name="B")
    log.add(at(20, 20), EV_MEDIA, title="Hidden")
    log.add(at(20, 30), EV_INPUT, source="HDMI 1")
    log.add(at(20, 40), EV_ADULT_MODE, on=False)
    log.add(at(20, 40), EV_APP, app="b", name="B")
    periods = log.adult_periods(at(0), at(0, day=9), at(23))
    kept = hide_adult_detail(log.events, periods)
    assert [(e["t"][11:16], e["type"]) for e in kept] == [
        ("20:00", "app"), ("20:10", "adult_mode"), ("20:30", "input"), ("20:40", "adult_mode"), ("20:40", "app"),
    ]


def test_subtract_and_overlap():
    seg = {"start": at(19).isoformat(), "end": at(22).isoformat(), "seconds": 3 * 3600,
           "live": True, "name": "A"}
    periods = [{"start": at(20).isoformat(), "end": at(20, 30).isoformat()},
               {"start": at(21, 45).isoformat(), "end": at(22).isoformat()}]
    pieces = subtract_periods([seg], periods)
    assert [(p["start"][11:16], p["end"][11:16], p["seconds"], p["live"]) for p in pieces] == [
        ("19:00", "20:00", 3600, False), ("20:30", "21:45", 4500, False),
    ]
    assert all(p["name"] == "A" for p in pieces)
    assert overlap_seconds([seg], periods) == 45 * 60
    assert subtract_periods([seg], []) == [seg]


def test_adult_totals_in_days_and_summary():
    log = ActivityLog()
    log.daily["2026-10-06"] = {"used_seconds": 600, "budget_minutes": 0, "extension_minutes": 0, "blocked": 0}
    log.record_day(date(2026, 10, 7), used_seconds=3600, budget_minutes=60, extension_minutes=0,
                   blocked=0, adult_seconds=1800)
    log.record_day(date(2026, 10, 8), used_seconds=3600, budget_minutes=60, extension_minutes=0,
                   blocked=0, adult_seconds=1200, adult_counted_seconds=1200)
    assert log.daily["2026-10-07"]["adult_seconds"] == 1800
    assert "adult_counted_seconds" not in log.daily["2026-10-07"]
    series = log.daily_series(date(2026, 10, 8), 3)
    assert [(d["adult_tracked"], d["adult_seconds"], d["adult_counted_seconds"]) for d in series] == [
        (False, 0, 0), (True, 1800, 0), (True, 1200, 1200),
    ]
    summary = summarize(series)
    assert summary["adult_seconds"] == 3000 and summary["adult_counted_seconds"] == 1200
    assert summary["adult_untracked_days"] == 1
    # Over the limit is screen time only: adult time is in used_seconds just when it counted.
    assert summary["days_over_limit"] == 2
    assert summary["total_seconds"] == 600 + 3600 + 3600
