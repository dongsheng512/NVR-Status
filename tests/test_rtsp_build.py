"""短时 RTSP 构造：时区模式检测 / 候选重试 / 防 400 窗越界。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from hikvision_status import HikvisionNVR


TZ_CN = timezone(timedelta(hours=8))


def _nvr() -> HikvisionNVR:
    n = HikvisionNVR(
        ip="192.168.1.64",
        port=80,
        username="admin",
        password="P@ss w0rd!",
        quiet=True,
    )
    n._device_tz = TZ_CN
    return n


def _uri(start_local: str, end_local: str) -> str:
    return (
        f"rtsp://192.168.1.64/Streaming/tracks/101/"
        f"?starttime={start_local}&endtime={end_local}&name=ch1&size=999999"
    )


def test_detect_mode_local_when_uri_matches_local_wall():
    n = _nvr()
    seg = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    assert n._detect_rtsp_time_mode("20260729T140000Z", seg) == "local"


def test_detect_mode_utc_when_uri_matches_true_utc():
    n = _nvr()
    seg = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    assert n._detect_rtsp_time_mode("20260729T060000Z", seg) == "utc"


def test_fmt_modes_differ_for_cn():
    n = _nvr()
    utc_dt = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    assert n._fmt_rtsp_time_mode(utc_dt, "local") == "20260729T140000Z"
    assert n._fmt_rtsp_time_mode(utc_dt, "utc") == "20260729T060000Z"


def test_candidates_rewrite_local_not_utc_offset_bug():
    n = _nvr()
    uri = _uri("20260729T140000Z", "20260729T150000Z")
    seg_s = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    seg_e = datetime(2026, 7, 29, 7, 0, 0, tzinfo=timezone.utc)
    clip_s = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    clip_e = clip_s + timedelta(seconds=6)

    cands = n._build_short_rtsp_candidates(
        uri, seg_s, seg_e, 6, clip_start=clip_s, clip_end=clip_e
    )
    assert cands
    labels = [c[0] for c in cands]
    assert "short/local" in labels
    assert "short/utc" in labels
    assert "original" in labels

    by = dict(cands)
    local_url = by["short/local"]
    assert "starttime=20260729T140000Z" in local_url
    assert "endtime=20260729T140006Z" in local_url
    assert "starttime=20260729T220000Z" not in local_url
    assert "size=" not in local_url
    assert "admin:" in local_url
    assert unquote(local_url.split("@")[0].split(":")[-1]) == "P@ss w0rd!"


def test_candidates_include_original_fallback():
    n = _nvr()
    uri = _uri("20260729T140000Z", "20260729T150000Z")
    cands = n._build_short_rtsp_candidates(uri, None, None, 6)
    assert cands[-1][0] == "original"
    assert "starttime=20260729T140000Z" in cands[-1][1]
    assert cands[-1][1].startswith("rtsp://admin:")


def test_legacy_build_short_rtsp_returns_first_candidate():
    n = _nvr()
    uri = _uri("20260729T140000Z", "20260729T150000Z")
    seg_s = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    seg_e = datetime(2026, 7, 29, 7, 0, 0, tzinfo=timezone.utc)
    one = n._build_short_rtsp(uri, seg_s, seg_e, 6)
    multi = n._build_short_rtsp_candidates(uri, seg_s, seg_e, 6)
    assert one == multi[0][1]


def test_inject_auth_replaces_existing_userinfo():
    n = _nvr()
    raw = (
        "rtsp://old:pw@192.168.1.1/Streaming/tracks/101/"
        "?starttime=20260729T140000Z&endtime=20260729T140006Z"
    )
    out = n._inject_rtsp_auth(raw)
    assert out.startswith("rtsp://admin:")
    assert "old:pw@" not in out
    assert "192.168.1.1/Streaming" in out


def test_compute_clip_window_clamps_outside_segment():
    n = _nvr()
    uri_s = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    uri_e = datetime(2026, 7, 29, 6, 10, 0, tzinfo=timezone.utc)
    far = datetime(2026, 7, 28, 0, 0, 0, tzinfo=timezone.utc)
    cs, ce = n._compute_clip_window(
        uri_s, uri_e, 6, clip_start=far, clip_end=far + timedelta(seconds=6)
    )
    assert cs == uri_s
    assert (ce - cs).total_seconds() >= 1
    assert ce <= uri_e
