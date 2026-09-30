"""回放 RTSP 的 :554 端口回退。

背景：CMSearch 返回的 playbackURI 端口是设备的 **HTTP** 端口（常见 :80）。
在部分环境下该端口的 RTSP 会被静默拒绝 —— TCP 三次握手正常、一发 DESCRIBE
就断开且不回任何数据，ffmpeg 只会报 `Invalid data found when processing input`；
而标准 RTSP 端口 :554 回同一个路径完全正常。所以候选列表需要一个「只换端口」
的兜底变体，并且在正常路径上不能拖慢抽检（必须排在最后）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hikvision_status import HikvisionNVR
from nvr_core.av_probe import av_candidate_order

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


def _uri(port: str = "") -> str:
    return (
        f"rtsp://192.168.1.64{port}/Streaming/tracks/101/"
        "?starttime=20260729T140000Z&endtime=20260729T150000Z&name=ch1"
    )


# ---------------------------------------------------------------- _swap_rtsp_port


def test_swap_port_rewrites_only_port():
    n = _nvr()
    out = n._swap_rtsp_port(_uri(":80"), 554)
    assert out is not None
    assert out.startswith("rtsp://192.168.1.64:554/Streaming/tracks/101/")
    assert "starttime=20260729T140000Z" in out
    assert "name=ch1" in out
    assert ":80" not in out


def test_swap_port_keeps_userinfo_and_encoded_password():
    n = _nvr()
    raw = _uri(":80").replace("rtsp://", "rtsp://admin:P%40ss%20w0rd%21@")
    out = n._swap_rtsp_port(raw, 554)
    assert out is not None
    assert out.startswith("rtsp://admin:P%40ss%20w0rd%21@192.168.1.64:554/")


def test_swap_port_none_when_no_explicit_port():
    n = _nvr()
    # 无显式端口时 ffmpeg 本来就默认走 :554，无需再补变体
    assert n._swap_rtsp_port(_uri(), 554) is None


def test_swap_port_none_when_already_target_port():
    n = _nvr()
    assert n._swap_rtsp_port(_uri(":554"), 554) is None


def test_swap_port_handles_ipv6_literal():
    n = _nvr()
    raw = "rtsp://admin:pw@[fe80::1]:80/Streaming/tracks/101/?starttime=1"
    out = n._swap_rtsp_port(raw, 554)
    assert out is not None
    assert out.startswith("rtsp://admin:pw@[fe80::1]:554/")
    # 无端口的 IPv6 也返回 None（默认端口即 :554）
    assert n._swap_rtsp_port("rtsp://admin:pw@[fe80::1]/Streaming", 554) is None


def test_swap_port_none_for_non_rtsp():
    n = _nvr()
    assert n._swap_rtsp_port("http://192.168.1.64:80/ISAPI", 554) is None
    assert n._swap_rtsp_port("", 554) is None


# ------------------------------------------------- _build_short_rtsp_candidates


def test_candidates_add_554_variants_when_uri_uses_http_port():
    n = _nvr()
    seg_s = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    seg_e = datetime(2026, 7, 29, 7, 0, 0, tzinfo=timezone.utc)
    clip_s = seg_s
    clip_e = clip_s + timedelta(seconds=6)

    cands = n._build_short_rtsp_candidates(
        _uri(":80"), seg_s, seg_e, 6, clip_start=clip_s, clip_end=clip_e
    )
    labels = [c[0] for c in cands]
    by = dict(cands)

    assert "short/local@554" in labels
    assert "short/utc@554" in labels
    assert "original@554" in labels
    # 每个 @554 变体 = 对应原候选只换端口，路径与查询串一致
    for base in ("short/local", "short/utc", "original"):
        assert by[base + "@554"] == n._swap_rtsp_port(by[base], 554)


def test_candidates_skip_554_variants_when_no_explicit_port():
    n = _nvr()
    seg_s = datetime(2026, 7, 29, 6, 0, 0, tzinfo=timezone.utc)
    seg_e = datetime(2026, 7, 29, 7, 0, 0, tzinfo=timezone.utc)
    cands = n._build_short_rtsp_candidates(_uri(), seg_s, seg_e, 6)
    assert cands
    assert not [c for c in cands if c[0].endswith("@554")]
    # 保持既有约定：最后一个候选是 original
    assert cands[-1][0] == "original"


def test_candidates_are_deduplicated():
    n = _nvr()
    cands = n._build_short_rtsp_candidates(_uri(":80"), None, None, 6)
    urls = [u for _l, u in cands]
    assert len(urls) == len(set(urls))


# ------------------------------------------------------------- av_candidate_order


def test_order_puts_554_variants_last():
    keys = [
        "short/local",
        "short/utc",
        "original",
        "short/local@554",
        "short/utc@554",
        "original@554",
    ]
    ordered = sorted(keys, key=av_candidate_order)
    assert ordered[:3] == ["original", "short/local", "short/utc"]
    assert all(k.endswith("@554") for k in ordered[3:])


def test_order_prefers_original_over_rewritten():
    cands = [("short/local", "u1"), ("original", "u2"), ("short/utc", "u3")]
    ordered = sorted(cands, key=lambda x: av_candidate_order(x[0]))
    assert [c[0] for c in ordered] == ["original", "short/local", "short/utc"]


def test_order_original_554_still_last_of_all():
    # 即便原 URI 只有 @554 变体可用，也不能排到非 554 的改写短窗前
    cands = [
        ("short/local", "u1"),
        ("original@554", "u2"),
    ]
    ordered = sorted(cands, key=lambda x: av_candidate_order(x[0]))
    assert [c[0] for c in ordered] == ["short/local", "original@554"]
