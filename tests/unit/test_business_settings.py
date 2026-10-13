from __future__ import annotations

import pytest

from systems._contract import SettingSpec
from worker import business_settings as bs


def test_core_settings_include_the_stale_job_threshold_at_four_hours():
    assert bs.spec("core.stale_job_alert_hours").default == 4  # decisions item 24, Oct 13


@pytest.mark.parametrize(
    ("spec", "raw", "value"),
    [
        (SettingSpec("x.n", "int", 1, "N", minimum=1, maximum=5), "3", 3),
        (SettingSpec("x.h", "number", 4, "H"), "2.5", 2.5),
        (SettingSpec("x.h", "number", 4, "H"), "2", 2),
        (SettingSpec("x.b", "bool", False, "B"), "on", True),
        (SettingSpec("x.b", "bool", False, "B"), "", False),
        (SettingSpec("x.c", "choice", "a", "C", choices=("a", "b")), "b", "b"),
        (SettingSpec("x.t", "text", "", "T"), "  hi  ", "hi"),
    ],
)
def test_parse_accepts_valid_form_values(spec, raw, value):
    assert bs.parse(spec, raw) == value


@pytest.mark.parametrize(
    ("spec", "raw", "message"),
    [
        (SettingSpec("x.n", "int", 1, "N", minimum=1, maximum=5), "9", "at most 5"),
        (SettingSpec("x.n", "int", 1, "N", minimum=1, maximum=5), "0", "at least 1"),
        (SettingSpec("x.n", "int", 1, "N"), "two", "not a valid int"),
        (SettingSpec("x.c", "choice", "a", "C", choices=("a", "b")), "z", "choose one of"),
        (SettingSpec("x.t", "text", "", "T"), "x" * 501, "500 characters"),
    ],
)
def test_parse_refuses_invalid_values(spec, raw, message):
    with pytest.raises(bs.InvalidSetting, match=message):
        bs.parse(spec, raw)


def test_unknown_settings_are_refused():
    with pytest.raises(bs.InvalidSetting, match="unknown setting"):
        bs.spec("core.nope")
