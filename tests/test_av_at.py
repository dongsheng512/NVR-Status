"""`--av-at`：定点抽检的贯通与选点行为。

链路与 `--av-channels` 同构：build_arg_parser → nvr_from_args →
scan_runner.build_nvr（预解析，非法降级）→ HikvisionNVR（datetime 直通）。

覆盖三层：
1. `parse_instant_list` 解析/校验/去重/回退天数；
2. CLI 参数 → HikvisionNVR.av_at 的贯通（含未开深度抽检时忽略、非法降级）；
3. `_pick_busy_clip_times` 定点选点：钉住指定时刻、距现在过近时前移并注明。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hikvision_status import build_arg_parser, nvr_from_args
from nvr_core.nvr import HikvisionNVR
from nvr_core.util import parse_instant_list

TZ = timezone.utc


# ------------------------------------------------------------- parse_instant_list


def test_parse_none_and_empty_mean_no_fixed_instant():
    assert parse_instant_list(None) is None
    assert parse_instant_list("") is None
    assert parse_instant_list("   ") is None


@pytest.mark.parametrize(
    "raw, hh, mm",
    [
        ("09:05", 9, 5),
        ("09:05,08:30", 9, 5),
        ("09:05、08:30", 9, 5),
        ("09:05，08:30", 9, 5),
        ("09:05;08:30", 9, 5),
        (" 09:05 , 08:30 ", 9, 5),
        ("9:05", 9, 5),  # 允许不补零的小时
    ],
)
def test_parse_accepts_common_separators(raw, hh, mm):
    now = datetime(2026, 9, 11, 12, 0).astimezone()
    out = parse_instant_list(raw, now=now)
    first = out[0].astimezone(now.tzinfo)
    assert (first.hour, first.minute) == (hh, mm)
    assert first.date() == now.date()


def test_parse_dedupes_and_keeps_order():
    now = datetime(2026, 9, 11, 12, 0).astimezone()
    out = parse_instant_list("09:05,09:05,08:30", now=now)
    assert len(out) == 2
    local = [t.astimezone(now.tzinfo).strftime("%H:%M") for t in out]
    assert local == ["09:05", "08:30"]  # 保留用户给定顺序,不擅自排序


@pytest.mark.parametrize("raw", ["25:00", "09:5x", "abc", "0905", "09:5"])
def test_parse_rejects_invalid_tokens(raw):
    with pytest.raises(ValueError):
        parse_instant_list(raw)


def test_parse_rejects_future_instant():
    """未来时刻直接报错:那一段录像还不存在,静默换成别的时刻会误导。"""
    now = datetime(2026, 9, 11, 12, 0).astimezone()
    with pytest.raises(ValueError, match="未来"):
        parse_instant_list("23:00", now=now)


def test_parse_days_ago_shifts_date():
    now = datetime(2026, 9, 11, 12, 0).astimezone()
    out = parse_instant_list("19:10", days_ago=1, now=now)
    first = out[0].astimezone(now.tzinfo)
    assert first.date() == now.date() - timedelta(days=1)


def test_parse_datetime_passthrough_is_idempotent():
    """build_nvr 预解析出 datetime 列表后,内部再解析一遍结果不变。"""
    now = datetime(2026, 9, 11, 12, 0).astimezone()
    first = parse_instant_list("09:05,08:30", now=now)
    second = parse_instant_list(first, now=now)
    assert first == second


def test_parse_rejects_unsupported_type():
    with pytest.raises(ValueError):
        parse_instant_list(3.5)


# ------------------------------------------------------------------ CLI 贯通


def _args(*extra):
    return build_arg_parser().parse_args(
        ["-i", "192.168.1.64", "-w", "pw", *extra]
    )


def test_cli_av_at_reaches_nvr():
    n = nvr_from_args(_args("--deep-av-check", "--av-at", "09:05,08:30"), quiet=True)
    assert n.av_at is not None and len(n.av_at) == 2
    first = n.av_at[0].astimezone()
    assert (first.hour, first.minute) == (9, 5)


def test_cli_av_at_absent_means_busy_hours_logic():
    n = nvr_from_args(_args("--deep-av-check"), quiet=True)
    assert n.av_at is None


def test_cli_av_at_with_busy_days_ago_lands_on_that_day():
    n = nvr_from_args(
        _args("--deep-av-check", "--av-at", "09:05", "--busy-days-ago", "1"),
        quiet=True,
    )
    first = n.av_at[0].astimezone()
    assert first.date() == datetime.now().astimezone().date() - timedelta(days=1)


def test_cli_av_at_ignored_without_deep_check(capsys):
    n = nvr_from_args(_args("--av-at", "09:05"), quiet=False)
    assert n.av_at is None
    out = capsys.readouterr().out
    assert "--av-at 已忽略" in out


def test_cli_av_at_invalid_warns_and_falls_back(capsys):
    n = nvr_from_args(_args("--deep-av-check", "--av-at", "25:00"), quiet=False)
    assert n.av_at is None
    out = capsys.readouterr().out
    assert "定点抽检时刻无效" in out


# ------------------------------------------------- _pick_busy_clip_times 定点选点


def _nvr_with_av_at(av_at) -> HikvisionNVR:
    return HikvisionNVR(
        ip="192.168.1.64",
        password="pw",
        quiet=True,
        deep_av_check=True,
        av_at=av_at,
    )


def test_pick_clip_times_pins_to_instant():
    """定点模式窗口起点 = 指定时刻,标签写明「定点抽检」。"""
    at = (datetime.now().astimezone() - timedelta(hours=2)).replace(
        second=0, microsecond=0
    )
    n = _nvr_with_av_at([at])
    s, e, label = n._pick_busy_clip_times(6)
    assert s.astimezone() == at
    assert (e - s).total_seconds() == 6
    assert "定点抽检" in label


def test_pick_clip_times_front_shifts_when_too_recent():
    """指定时刻距现在不足 10 分钟 → 前移到「现在−10分钟」,标签注明不静默。"""
    now = datetime.now().astimezone()
    at = now - timedelta(minutes=2)
    n = _nvr_with_av_at([at])
    s, e, label = n._pick_busy_clip_times(6)
    assert e <= now - timedelta(minutes=9)
    assert "已前移" in label


def test_pick_clip_times_explicit_at_overrides_first_instant():
    """多时刻循环逐个传 at=,选点应跟随显式参数而不是永远取第一个。"""
    t1 = datetime.now().astimezone() - timedelta(hours=3)
    t2 = datetime.now().astimezone() - timedelta(hours=1)
    n = _nvr_with_av_at([t1, t2])
    s, _, _ = n._pick_busy_clip_times(6, at=t2)
    assert s.astimezone() == t2


def test_pick_clip_times_label_keeps_requested_instant():
    """标签要同时给出「请求的时刻」和实际抽检点，别只报实际点。"""
    at = (datetime.now().astimezone() - timedelta(hours=3)).replace(microsecond=0)
    n = _nvr_with_av_at([at])
    _, _, label = n._pick_busy_clip_times(6)
    assert at.strftime("%H:%M:%S") in label
    assert "抽检点" in label


def test_pick_clip_times_label_says_shifted():
    now = datetime.now().astimezone()
    at = now - timedelta(minutes=2)
    n = _nvr_with_av_at([at])
    _, _, label = n._pick_busy_clip_times(6)
    assert "已前移" in label
    assert at.strftime("%H:%M:%S") in label, "前移后仍要能看到原本请求的时刻"
