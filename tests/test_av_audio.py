"""独立音频抽检：有声/无音轨/超时；异常通道换时段复检。"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from datetime import datetime, timedelta, timezone

from hikvision_status import HikvisionNVR
from nvr_core.av_probe import (
    _audio_verdict,
    _av_severity,
    _stderr_no_mapped_stream,
    av_needs_retry,
)
from nvr_core.util import alternate_clip_times
from tests.test_rtsp_build import TZ_CN


def _nvr() -> HikvisionNVR:
    n = HikvisionNVR(
        ip="192.168.1.64",
        port=80,
        username="admin",
        password="x",
        quiet=True,
        deep_av_check=True,
        av_seconds=3,
    )
    n._device_tz = TZ_CN
    n._tools = {"ffmpeg": "/bin/ffmpeg", "ffprobe": "/bin/ffprobe"}
    return n


def test_stderr_detects_no_audio_stream():
    assert _stderr_no_mapped_stream(
        "Stream map '0:a:0' matches no streams."
    )
    assert _stderr_no_mapped_stream(
        "Output file does not contain any stream"
    )
    assert not _stderr_no_mapped_stream("connection timed out")


def test_audio_verdict_ok_and_silence():
    assert _audio_verdict("ok", True, -20.0, -80.0) == ("正常", "")
    st, note = _audio_verdict("ok", True, -90.0, -80.0)
    assert st == "警告"
    assert "静音" in note


def test_audio_verdict_no_stream_vs_config():
    assert _audio_verdict("no_stream", True, None, -80.0) == ("异常", "无音轨")
    assert _audio_verdict("no_stream", None, None, -80.0) == ("异常", "无音轨")
    st, note = _audio_verdict("no_stream", False, None, -80.0)
    assert st == "跳过"
    assert "未开" in note


def test_audio_verdict_timeout_is_unknown_not_anomaly():
    st, note = _audio_verdict("fail", True, None, -80.0)
    assert st == "未知"
    assert "未确认" in note


def test_pull_rtsp_map_stops_on_no_stream(tmp_path):
    n = _nvr()
    dest = tmp_path / "a.mkv"
    dest.write_bytes(b"")

    def fake_run(cmd, timeout):
        return SimpleNamespace(returncode=1, stderr="Stream map '0:a:0' matches no streams.\n")

    n._run_cancellable = fake_run  # type: ignore[method-assign]
    status, size, err, used = n._pull_rtsp_map(
        "/bin/ffmpeg",
        [("original", "rtsp://nvr/Streaming/tracks/101/")],
        str(dest),
        "0:a:0",
        3,
        extra_args=["-vn"],
        min_ok=512,
        stop_on_no_stream=True,
    )
    assert status == "no_stream"
    assert used is None
    assert "matches no streams" in err.lower()
    assert size == 0


def test_pull_rtsp_map_ok_when_file_large_enough(tmp_path):
    n = _nvr()
    dest = tmp_path / "a.mkv"
    dest.write_bytes(b"")

    def fake_run(cmd, timeout):
        assert "-map" in cmd and "0:a:0" in cmd
        assert "-vn" in cmd
        dest.write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    n._run_cancellable = fake_run  # type: ignore[method-assign]
    status, size, err, used = n._pull_rtsp_map(
        "/bin/ffmpeg",
        [("original", "rtsp://nvr/x")],
        str(dest),
        "0:a:0",
        3,
        extra_args=["-vn"],
        min_ok=512,
        partial_ok=1024,
        stop_on_no_stream=True,
    )
    assert status == "ok"
    assert size >= 512
    assert used == ("original", "rtsp://nvr/x")
    assert err == ""


def test_pull_rtsp_map_timeout_is_fail_not_no_stream(tmp_path):
    n = _nvr()
    dest = tmp_path / "a.mkv"
    dest.write_bytes(b"")

    def fake_run(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    n._run_cancellable = fake_run  # type: ignore[method-assign]
    status, _size, err, used = n._pull_rtsp_map(
        "/bin/ffmpeg",
        [("original", "rtsp://nvr/x")],
        str(dest),
        "0:a:0",
        3,
        extra_args=["-vn"],
        min_ok=512,
        partial_ok=1024,
        timeout_original=4,
        stop_on_no_stream=True,
    )
    assert status == "fail"
    assert used is None
    assert "超时" in err


def test_av_needs_retry_only_for_failures():
    assert av_needs_retry({"视频抽检": "异常", "音频抽检": "跳过"})
    assert av_needs_retry({"视频抽检": "正常", "音频抽检": "未知"})
    assert av_needs_retry({
        "视频抽检": "跳过",
        "音频抽检": "跳过",
        "抽检详情": "无回放URI",
    })
    assert not av_needs_retry({"视频抽检": "正常", "音频抽检": "正常"})
    assert not av_needs_retry({"视频抽检": "正常", "音频抽检": "警告"})
    assert not av_needs_retry({
        "视频抽检": "跳过",
        "音频抽检": "跳过",
        "抽检详情": "未配置录像",
    })


def test_av_severity_retry_improves():
    assert _av_severity("正常", "正常") < _av_severity("异常", "跳过")
    assert _av_severity("正常", "警告") < _av_severity("正常", "未知")


def test_alternate_clip_is_at_least_30_min_apart():
    win_s = datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc)
    win_e = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
    avoid_s = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    alt = alternate_clip_times(win_s, win_e, 6, avoid_s, cutoff)
    assert alt is not None
    cs, ce = alt
    assert abs((cs - avoid_s).total_seconds()) >= 30 * 60
    assert win_s <= cs < ce <= min(win_e, cutoff)


def test_alternate_clip_none_when_window_too_small():
    win_s = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)
    win_e = datetime(2026, 9, 10, 6, 10, tzinfo=timezone.utc)
    assert alternate_clip_times(win_s, win_e, 6, win_s, win_e) is None


def test_row_tag_unknown_audio_is_warn():
    from ui.widgets.channel_table import row_tag
    from tests.test_channel_table import _rec

    assert row_tag(_rec(1, achk="未知"), True) == "warn"
    assert row_tag(_rec(1, achk="异常"), True) == "error"
