import pytest

from tv_mgmt_pure.names import InputNames, clean_names, format_names, parse_names


def test_parse_and_format_round_trip():
    names = parse_names("com.tcl.tv = Apple TV\n\n  HDMI 1=Xbox  \n")
    assert names == {"com.tcl.tv": "Apple TV", "HDMI 1": "Xbox"}
    assert parse_names(format_names(names)) == names


@pytest.mark.parametrize("bad", ["com.tcl.tv", "= Apple TV", "com.tcl.tv ="])
def test_parse_rejects_bad_lines(bad):
    with pytest.raises(ValueError):
        parse_names(bad)


def test_names_prefer_user_then_known_then_raw():
    names = InputNames({"com.tcl.tv": "Apple TV", "com.netflix.ninja": "Movies"})
    assert names.name("com.tcl.tv") == "Apple TV"
    assert names.name("com.netflix.ninja") == "Movies"
    assert names.name("com.google.android.apps.tv.launcherx") == "Google TV home"
    assert names.name("HDMI 3") == "HDMI 3"
    assert names.name(None) is None
    assert names.label("com.tcl.tv") == "Apple TV (com.tcl.tv)"
    assert names.label("HDMI 3") == "HDMI 3"


def test_clean_drops_blank_and_same_names():
    assert clean_names({"a": "", "": "x", "b": "b", "c": " C "}) == {"c": "C"}
