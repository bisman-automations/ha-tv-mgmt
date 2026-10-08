from datetime import time

from tv_mgmt_pure.quiet import parse_windows
from tv_mgmt_pure.state import (
    MODE_ENFORCED,
    MODE_MONITOR_ONLY,
    MODE_PAUSED,
    REASON_BUDGET,
    REASON_MANUAL,
    REASON_QUIET,
    STATE_ADULT_MODE,
    STATE_ENFORCING,
    STATE_OK,
    STATE_PAUSED,
    STATE_WARNING,
    decide,
)


def run(**overrides):
    args = dict(
        now=time(16, 0),
        mode=MODE_ENFORCED,
        adult_mode=False,
        force_block=False,
        budget_minutes=60,
        extension_minutes=0,
        used_seconds=0,
        warn_minutes=5,
        quiet_windows=[],
    )
    args.update(overrides)
    return decide(**args)


def test_ok_with_time_left():
    d = run(used_seconds=10 * 60)
    assert d.state == STATE_OK
    assert d.remaining_seconds == 50 * 60
    assert not d.blocked


def test_warning_near_limit():
    d = run(used_seconds=56 * 60)
    assert d.state == STATE_WARNING
    assert d.reason == REASON_BUDGET


def test_enforcing_when_out_of_time():
    d = run(used_seconds=61 * 60)
    assert d.state == STATE_ENFORCING
    assert d.reason == REASON_BUDGET
    assert d.remaining_seconds == 0
    assert d.blocked


def test_extension_adds_time():
    assert run(used_seconds=61 * 60, extension_minutes=30).state == STATE_OK


def test_negative_extension_removes_time():
    assert run(used_seconds=40 * 60, extension_minutes=-30).state == STATE_ENFORCING


def test_unlimited_budget():
    d = run(budget_minutes=0, used_seconds=10 * 3600)
    assert d.state == STATE_OK
    assert d.remaining_seconds is None


def test_quiet_window_blocks_even_with_time_left():
    d = run(now=time(21, 0), quiet_windows=parse_windows("20:30-07:00 Bedtime"))
    assert d.state == STATE_ENFORCING
    assert d.reason == REASON_QUIET
    assert d.quiet_window == "Bedtime"


def test_force_block_wins_over_quiet_window():
    d = run(force_block=True, now=time(21, 0), quiet_windows=parse_windows("20:30-07:00"))
    assert d.reason == REASON_MANUAL


def test_adult_mode_lifts_everything():
    d = run(adult_mode=True, force_block=True, used_seconds=999 * 60)
    assert d.state == STATE_ADULT_MODE
    assert not d.blocked


def test_paused_lifts_everything():
    assert run(mode=MODE_PAUSED, force_block=True).state == STATE_PAUSED


def test_monitor_only_still_reports_enforcing():
    # The caller decides not to act; the decision itself is unchanged.
    assert run(mode=MODE_MONITOR_ONLY, used_seconds=61 * 60).state == STATE_ENFORCING
