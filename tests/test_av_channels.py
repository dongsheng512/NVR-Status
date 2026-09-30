"""`--av-channels`：CLI 参数 → 选项 → HikvisionNVR 的贯通，以及通道列表解析。

链路：build_arg_parser → nvr_from_args → scan_runner.build_nvr → HikvisionNVR
（HikvisionNVR.__init__ 里由 parse_channel_list 统一归一化）。
"""

from __future__ import annotations

import pytest

from hikvision_status import build_arg_parser, nvr_from_args
from nvr_core.util import parse_channel_list


# ------------------------------------------------------------- parse_channel_list


def test_parse_none_and_empty_mean_no_filter():
    assert parse_channel_list(None) is None
    assert parse_channel_list("") is None
    assert parse_channel_list("   ") is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("31,64", {31, 64}),
        ("31 64", {31, 64}),
        ("31、64", {31, 64}),
        ("31，64", {31, 64}),
        ("31;64", {31, 64}),
        (" 31 , 64 ", {31, 64}),
        ("64,31", {64, 31}),
        ("31,31,64", {31, 64}),  # 去重
        ([31, 64], {31, 64}),
        ((31, 64), {31, 64}),
        (31, {31}),
    ],
)
def test_parse_accepts_common_separators_and_types(raw, expected):
    assert parse_channel_list(raw) == frozenset(expected)


@pytest.mark.parametrize("raw", ["abc", "31,abc", "0", "-3", "31,0", [0], ["31x"]])
def test_parse_rejects_invalid_tokens_instead_of_silently_dropping(raw):
    """写错的通道号必须报错：静默丢弃会让人以为查过某通道，其实没查。"""
    with pytest.raises(ValueError):
        parse_channel_list(raw)


def test_parse_rejects_bool():
    # True 是 int，若不拦会被当成通道 1
    with pytest.raises(ValueError):
        parse_channel_list(True)


def test_parse_rejects_unsupported_type():
    with pytest.raises(ValueError):
        parse_channel_list(3.5)


# ------------------------------------------------------------------ CLI 贯通


def _args(*extra):
    return build_arg_parser().parse_args(
        ["-i", "192.168.1.64", "-w", "pw", *extra]
    )


def test_cli_av_channels_reaches_nvr():
    n = nvr_from_args(
        _args("--deep-av-check", "--av-channels", "31,64"), quiet=True
    )
    assert n.av_channels == frozenset({31, 64})
    assert n.deep_av_check is True


def test_cli_av_channels_absent_means_all_channels():
    n = nvr_from_args(_args("--deep-av-check"), quiet=True)
    assert n.av_channels is None


def test_cli_av_channels_with_av_save_counts_as_deep(capsys):
    n = nvr_from_args(_args("--av-save", "--av-channels", "31"), quiet=True)
    assert n.av_channels == frozenset({31})


def test_cli_av_channels_ignored_without_deep_check(capsys):
    """没开深度抽检时通道过滤无从作用，必须提示而不是静默留着。"""
    n = nvr_from_args(_args("--av-channels", "31,64"), quiet=False)
    assert n.av_channels is None
    out = capsys.readouterr().out
    assert "--av-channels 已忽略" in out


def test_cli_av_channels_invalid_warns_and_falls_back_to_all(capsys):
    n = nvr_from_args(
        _args("--deep-av-check", "--av-channels", "31,abc"), quiet=False
    )
    assert n.av_channels is None
    out = capsys.readouterr().out
    assert "抽检通道过滤无效" in out
