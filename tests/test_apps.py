import pytest

from tv_mgmt_pure.apps import (
    APP_MODE_ALLOW,
    APP_MODE_BLOCK,
    REASON_BLOCKED,
    REASON_LIMIT,
    REASON_NOT_ALLOWED,
    AppRules,
    format_limits,
    is_home,
    parse_limits,
)

YT = ("com.google.ios.youtube", "YouTube")
DISNEY = ("com.disney.disneyplus", "Disney+")


def test_parse_limits_round_trip():
    limits = parse_limits("YouTube = 30\n\ncom.netflix.Netflix=60\n")
    assert limits == {"YouTube": 30, "com.netflix.Netflix": 60}
    assert parse_limits(format_limits(limits)) == limits


@pytest.mark.parametrize("bad", ["YouTube", "YouTube = ", "= 30", "YouTube = half"])
def test_parse_limits_rejects_bad_lines(bad):
    with pytest.raises(ValueError):
        parse_limits(bad)


def test_app_names_with_equals_sign():
    assert parse_limits("A = B = 15") == {"A = B": 15}


def test_home_screen_is_never_blocked():
    rules = AppRules(mode=APP_MODE_ALLOW, apps=["Disney+"])
    assert is_home(None) and is_home("com.apple.HeadBoard")
    assert rules.check("com.apple.HeadBoard", "Home", 0) is None
    assert rules.check(None, None, 0) is None


def test_block_mode_matches_id_or_name_case_insensitive():
    rules = AppRules(mode=APP_MODE_BLOCK, apps=["youtube"])
    assert rules.check(*YT, 0) == REASON_BLOCKED
    assert rules.check(*DISNEY, 0) is None
    assert AppRules(apps=["com.google.ios.youtube"]).check(*YT, 0) == REASON_BLOCKED


def test_allow_mode():
    rules = AppRules(mode=APP_MODE_ALLOW, apps=["Disney+"])
    assert rules.check(*DISNEY, 0) is None
    assert rules.check(*YT, 0) == REASON_NOT_ALLOWED


def test_per_app_limits():
    rules = AppRules(limits={"YouTube": 30})
    assert rules.check(*YT, 29 * 60) is None
    assert rules.check(*YT, 30 * 60) == REASON_LIMIT
    assert rules.check(*DISNEY, 999 * 60) is None
    assert rules.limit_for(*YT) == 30


def test_active():
    assert not AppRules().active
    assert AppRules(mode=APP_MODE_ALLOW).active
    assert AppRules(limits={"YouTube": 5}).active
