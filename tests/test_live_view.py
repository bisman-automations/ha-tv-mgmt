"""What the TV's Live Activity shows, and when it's worth a push."""

from tv_mgmt_pure.live_view import (
    TONE_BAD,
    TONE_OFF,
    TONE_OK,
    TONE_WARN,
    LiveView,
    clock_text,
    duration,
    live_view,
    same_view,
)
from tv_mgmt_pure.state import (
    REASON_BUDGET,
    REASON_MANUAL,
    REASON_QUIET,
    STATE_ENFORCING,
    STATE_OK,
    STATE_PAUSED,
    STATE_WARNING,
)

NOW = 1_800_000_000  # epoch seconds, on a whole minute


def view(**overrides):
    args = dict(
        on=True,
        state=STATE_OK,
        reason=None,
        limit=REASON_BUDGET,
        remaining_seconds=45 * 60,
        used_seconds=30 * 60,
        quiet_window=None,
        now=NOW,
        clock=lambda epoch: "7:45 PM",
    )
    args.update(overrides)
    return live_view(**args)


def test_time_left_counts_down_to_when_it_runs_out():
    v = view()
    assert v == LiveView(
        "Time left today", "TV time ends at 7:45 PM.", TONE_OK, NOW + 45 * 60
    )
    assert view(state=STATE_WARNING, remaining_seconds=4 * 60).tone == TONE_WARN
    assert "whole home" in view(limit="home_daily").message


def test_other_states():
    assert view(on=False).headline == "TV off"
    assert view(on=False).message == "30 min watched today"
    assert view(on=False).tone == TONE_OFF
    assert view(remaining_seconds=None, limit=None).headline == "No limit today"
    assert view(state=STATE_PAUSED).headline == "Rules paused"
    up = view(state=STATE_ENFORCING, reason=REASON_BUDGET, remaining_seconds=0)
    assert (up.headline, up.tone, up.ends_at) == ("Time's up", TONE_BAD, None)
    assert view(state=STATE_ENFORCING, reason=REASON_MANUAL).headline == "Blocked"
    quiet = view(state=STATE_ENFORCING, reason=REASON_QUIET, quiet_window="Bedtime")
    assert quiet.message.startswith("Bedtime")


def test_only_visible_changes_need_a_push():
    a = view()
    assert same_view(a, view(now=NOW + 30, remaining_seconds=45 * 60 - 20))
    assert not same_view(a, view(remaining_seconds=75 * 60))  # extra time
    assert not same_view(a, view(state=STATE_WARNING))
    assert not same_view(a, view(on=False))
    assert not same_view(None, a)


def test_text():
    assert duration(45 * 60) == "45 min"
    assert duration(65 * 60) == "1 h 5 min"
    assert duration(120 * 60) == "2 h"
    assert clock_text(0, 5) == "12:05 AM"
    assert clock_text(19, 45) == "7:45 PM"
