"""A4-4 lookback 分钟↔数值/单位 换算（纯函数，无 UI）。"""

from __future__ import annotations

import pytest

from ui.main_window import _lookback_minutes_of, _lookback_value_for_minutes


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (60, "minute", 60),
        (2, "hour", 120),
        (3, "day", 4320),
        (1, "day", 1440),
        (0, "minute", 1),  # 下限保护
        (5, "unknown-unit", 5),  # 未知单位回退分钟
    ],
)
def test_lookback_minutes_of(value, unit, expected):
    assert _lookback_minutes_of(value, unit) == expected


@pytest.mark.parametrize(
    "minutes,unit,expected",
    [
        (60, "minute", (60, "minute")),
        (120, "hour", (2, "hour")),
        (4320, "day", (3, "day")),
        (90, None, (90, "minute")),        # 不能整除 → 分钟
        (1440, None, (1, "day")),          # 能整除 → 天
        (7200, "hour", (120, "hour")),     # 显式单位不推断
        (130, "hour", (3, "hour")),        # 向上取整，窗口不小
    ],
)
def test_lookback_value_for_minutes(minutes, unit, expected):
    assert _lookback_value_for_minutes(minutes, unit) == expected


def test_lookback_roundtrip():
    # 任意分钟 → 数值/单位 → 分钟，结果不小于原值
    for minutes in (30, 61, 119, 121, 1439, 1441, 2880):
        value, unit = _lookback_value_for_minutes(minutes)
        back = _lookback_minutes_of(value, unit)
        assert back >= minutes
