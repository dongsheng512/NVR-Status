"""音视频抽检端到端集成：真实启动子进程，只有媒体源是假的。

现有 `test_av_audio.py` 只覆盖到判定函数与 `_pull_rtsp_map`（且把
`_run_cancellable` 整个替换掉）；本文件补的是它完全没碰到的那条链路：

    _run_deep_av_checks → _run_av_jobs → _probe_channel_at → _probe_track_av
        ├─ _build_short_rtsp_candidates（候选排序）
        ├─ _run_cancellable（真 Popen + 超时 + 取消）
        ├─ ffprobe 解析 → 分辨率判定
        └─ _probe_audio_track（独立 -map 0:a:0 + volumedetect）

做法：在临时目录生成 `ffmpeg` / `ffprobe` 两个 shell 脚本，由环境变量控制行为，
并把每次调用的命令行追加到 FAKE_LOG。因此既能断言「结果对不对」，也能断言
「命令行是不是按预期构造的」——例如视频走 `-map 0:v:0`、音频走独立
`-map 0:a:0 -vn`，这是本次未提交改动的核心，之前没有任何测试守着它。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from hikvision_status import HikvisionNVR
from nvr_core.util import ScanCancelled
from tests.test_rtsp_build import TZ_CN

TZ = timezone.utc
CLIP_S = datetime(2026, 9, 10, 6, 0, 0, tzinfo=TZ)

# 假 ffmpeg：按 -map/-vn 判断是视频还是音频调用；volumedetect 单独处理。
FFMPEG_SH = r"""#!/bin/sh
out=""
mode="pull"
audio=0
prev=""
inp=""
for a in "$@"; do
  case "$a" in
    -vn) audio=1 ;;
    volumedetect) mode="vol" ;;
  esac
  [ "$prev" = "-i" ] && inp="$a"
  prev="$a"
  out="$a"
done

if [ -n "${FAKE_LOG:-}" ]; then
  printf '%s\n' "$*" >> "$FAKE_LOG"
fi

if [ "$mode" = "vol" ]; then
  if [ -n "${FAKE_MEAN_DB:-}" ]; then
    echo "[Parsed_volumedetect_0 @ 0x55f] mean_volume: ${FAKE_MEAN_DB} dB" >&2
  fi
  exit 0
fi

if [ "$audio" = "1" ]; then
  st="${FAKE_AUDIO_MODE:-ok}"
else
  st="${FAKE_VIDEO_MODE:-ok}"
fi

# URI 里带 FAILME 的输入一律拉流失败（用于验证换时段复检）
if [ "${inp#*FAILME}" != "$inp" ]; then
  st="fail"
fi

case "$st" in
  ok)
    dd if=/dev/zero of="$out" bs=1024 count=16 2>/dev/null
    ;;
  small)
    dd if=/dev/zero of="$out" bs=1 count=100 2>/dev/null
    ;;
  nostream)
    : > "$out"
    echo "Stream map '0:a:0' matches no streams." >&2
    ;;
  *)
    : > "$out"
    echo "Failed to open input: $inp" >&2
    ;;
esac
exit 0
"""

# 假 ffprobe：带 -show_format 的是视频探测，否则是音频探测。
FFPROBE_SH = r"""#!/bin/sh
rc="${FAKE_FFPROBE_RC:-0}"
if [ "$rc" != "0" ]; then
  echo "probe: invalid data found" >&2
  exit "$rc"
fi
has_fmt=0
for a in "$@"; do
  [ "$a" = "-show_format" ] && has_fmt=1
done
if [ "$has_fmt" = "1" ]; then
  v="${FAKE_VIDEO_JSON:-}"
  [ -z "$v" ] && v='{"streams":[]}'
  printf '%s' "$v"
else
  a="${FAKE_AUDIO_JSON:-}"
  [ -z "$a" ] && a='{"streams":[]}'
  printf '%s' "$a"
fi
exit 0
"""

VIDEO_OK_JSON = json.dumps({
    "streams": [{"codec_type": "video", "codec_name": "h264",
                 "width": 1920, "height": 1080}],
    "format": {"format_name": "matroska,webm"},
})
AUDIO_OK_JSON = json.dumps({
    "streams": [{"codec_type": "audio", "codec_name": "aac"}],
})


def _install_fakes(tmp_path: Path) -> Dict[str, str]:
    """生成假 ffmpeg/ffprobe，返回 {"ffmpeg":…, "ffprobe":…, "log":…}。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    ffmpeg = bin_dir / "ffmpeg"
    ffprobe = bin_dir / "ffprobe"
    ffmpeg.write_text(FFMPEG_SH, encoding="utf-8")
    ffprobe.write_text(FFPROBE_SH, encoding="utf-8")
    os.chmod(ffmpeg, 0o755)
    os.chmod(ffprobe, 0o755)
    return {
        "ffmpeg": str(ffmpeg),
        "ffprobe": str(ffprobe),
        "log": str(tmp_path / "calls.log"),
    }


def _client(
    tmp_path: Path,
    monkeypatch,
    *,
    video: str = "ok",
    audio: str = "ok",
    mean_db: str = "-42.5",
    video_json: str = "",
    audio_json: str = "",
    password: str = "b",
    av_channels=None,
) -> HikvisionNVR:
    fakes = _install_fakes(tmp_path)
    monkeypatch.setenv("FAKE_VIDEO_MODE", video)
    monkeypatch.setenv("FAKE_AUDIO_MODE", audio)
    monkeypatch.setenv("FAKE_MEAN_DB", mean_db)
    monkeypatch.setenv("FAKE_VIDEO_JSON", video_json or VIDEO_OK_JSON)
    monkeypatch.setenv("FAKE_AUDIO_JSON", audio_json or AUDIO_OK_JSON)
    monkeypatch.setenv("FAKE_LOG", fakes["log"])
    monkeypatch.delenv("FAKE_FFPROBE_RC", raising=False)

    n = HikvisionNVR(
        ip="192.168.1.64",
        username="admin",
        password=password,
        quiet=True,
        deep_av_check=True,
        av_seconds=3,
        av_workers=1,
        av_channels=av_channels,
    )
    n._device_tz = TZ_CN
    n._tools = {"ffmpeg": fakes["ffmpeg"], "ffprobe": fakes["ffprobe"]}
    n._fake_log = fakes["log"]  # type: ignore[attr-defined]
    # 抽检时段固定，避免依赖真实时钟；定点模式(at=)直接钉在指定时刻
    n._pick_busy_clip_times = lambda seconds, at=None: (  # type: ignore[method-assign]
        (at or CLIP_S),
        (at or CLIP_S) + timedelta(seconds=seconds),
        (
            "测试定点窗; 抽检点 " + (at or CLIP_S).astimezone().strftime("%m-%d %H:%M:%S")
            if at is not None
            else "测试繁忙窗; 抽检点 09-10 14:00:00(14:00附近)"
        ),
    )
    n._pick_retry_clip_times = lambda seconds, a, b: (  # type: ignore[method-assign]
        CLIP_S + timedelta(hours=1), CLIP_S + timedelta(hours=1, seconds=seconds),
        "测试繁忙窗; 复检点 09-10 15:00:00(换时段)",
    )
    return n


def _rec(
    tid: str,
    *,
    ch: Optional[str] = None,
    name: Optional[str] = None,
    audio: Optional[bool] = True,
    enabled: bool = True,
    disk: str = "正常",
) -> Dict:
    return {
        "track_id": str(tid),
        "通道": str(ch if ch is not None else tid),
        "名称": name or f"cam{tid}",
        "在线": "true",
        "已启用录像": enabled,
        "录像模式": "连续录像",
        "录像时段": "1/1",
        "录像含音频": audio,
        "落盘状态": disk,
        "落盘详情": "",
        "playback_uri": None,
        "seg_start": None,
        "seg_end": None,
        "视频抽检": "跳过",
        "音频抽检": "跳过",
        "抽检详情": "未启用深度抽检",
        "录像是否正常": None,
    }


def _uri(tid: str, host: str = "192.168.1.64") -> str:
    return (
        f"rtsp://{host}/Streaming/tracks/{tid}/"
        f"?starttime=20260910T140000Z&endtime=20260910T150000Z&name=ch1&size=999999"
    )


def _found(tid: str, host: str = "192.168.1.64") -> Dict:
    return {
        "ok": True,
        "playback_uri": _uri(tid, host),
        "seg_start": CLIP_S,
        "seg_end": CLIP_S + timedelta(minutes=10),
        "detail": "ok",
    }


def _log_lines(n: HikvisionNVR) -> List[str]:
    p = Path(n._fake_log)  # type: ignore[attr-defined]
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8").splitlines()


# ---------- 全链路 ----------


def test_full_chain_ok_splits_video_and_audio_commands(tmp_path, monkeypatch):
    """视频正常 + 音频正常：字段齐备，且视频/音频确实是两条独立命令。"""
    n = _client(tmp_path, monkeypatch)
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [_rec("101", name="前门")]

    n._run_deep_av_checks(recs)

    r = recs[0]
    assert r["视频抽检"] == "正常"
    assert r["音频抽检"] == "正常"
    assert r["video_codec"] == "h264"
    assert r["audio_codec"] == "aac"
    assert r["resolution"] == "1920x1080"
    assert r["mean_volume_db"] == -42.5
    assert "短时抽检OK@09-10 14:00:00" in r["抽检详情"]
    assert "1920x1080" in r["抽检详情"]
    assert "aac" in r["抽检详情"]

    lines = _log_lines(n)
    video_cmds = [l for l in lines if "-map 0:v:0" in l]
    audio_cmds = [l for l in lines if "-map 0:a:0" in l]
    assert video_cmds, "没有发出视频拉流命令"
    assert audio_cmds, "没有发出独立音频拉流命令"
    # 视频不带 -vn；音频必须带 -vn（否则会把视频一起拉下来）
    assert all("-vn" not in l for l in video_cmds)
    assert all("-vn" in l for l in audio_cmds)
    # 独立音频轨必须单独探测音量
    assert any("volumedetect" in l for l in lines)


def test_probe_track_av_used_original_uri_first(tmp_path, monkeypatch):
    """候选排序：原 URI 优先（球机短窗 seek 慢，原段更稳）。"""
    n = _client(tmp_path, monkeypatch)
    res = n._probe_track_av(
        track_id="101",
        playback_uri=_uri("101"),
        seg_start=CLIP_S,
        seg_end=CLIP_S + timedelta(minutes=10),
        expect_audio=True,
        clip_start=CLIP_S,
        clip_end=CLIP_S + timedelta(seconds=3),
        sample_label="测试; 抽检点 09-10 14:00:00",
    )
    assert res["视频抽检"] == "正常"
    first = _log_lines(n)[0]
    assert "-map 0:v:0" in first
    assert "/Streaming/tracks/101/" in first


# ---------- 视频异常分支 ----------


def test_low_resolution_is_anomaly_and_audio_skipped(tmp_path, monkeypatch):
    low = json.dumps({"streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 64, "height": 48},
    ]})
    n = _client(tmp_path, monkeypatch, video_json=low)
    res = n._probe_track_av(
        track_id="101", playback_uri=_uri("101"),
        seg_start=CLIP_S, seg_end=CLIP_S + timedelta(minutes=10),
        expect_audio=True,
    )
    assert res["视频抽检"] == "异常"
    assert "分辨率异常" in res["抽检详情"]
    assert res["音频抽检"] == "跳过"
    # 视频都不正常了，不该再去拉音频
    assert not [l for l in _log_lines(n) if "-map 0:a:0" in l]


def test_no_video_stream_is_anomaly(tmp_path, monkeypatch):
    n = _client(tmp_path, monkeypatch, video_json=json.dumps({"streams": []}))
    res = n._probe_track_av(
        track_id="101", playback_uri=_uri("101"),
        seg_start=CLIP_S, seg_end=CLIP_S + timedelta(minutes=10),
        expect_audio=True,
    )
    assert res["视频抽检"] == "异常"
    assert res["抽检详情"] == "无视频轨"
    assert res["音频抽检"] == "跳过"


def test_ffprobe_failure_is_anomaly(tmp_path, monkeypatch):
    n = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_FFPROBE_RC", "1")
    res = n._probe_track_av(
        track_id="101", playback_uri=_uri("101"),
        seg_start=CLIP_S, seg_end=CLIP_S + timedelta(minutes=10),
        expect_audio=True,
    )
    assert res["视频抽检"] == "异常"
    assert res["抽检详情"] == "ffprobe解析失败"


def test_all_pull_failures_mask_credentials(tmp_path, monkeypatch):
    """拉流失败时，stderr 里的 rtsp 明文口令必须被掩掉才进详情。"""
    secret = "P@ss w0rd!"
    n = _client(tmp_path, monkeypatch, video="fail", password=secret)
    res = n._probe_track_av(
        track_id="101", playback_uri=_uri("101"),
        seg_start=CLIP_S, seg_end=CLIP_S + timedelta(minutes=10),
        expect_audio=False,
    )
    detail = res["抽检详情"]
    assert res["视频抽检"] == "异常"
    assert "短时拉流失败" in detail
    assert secret not in detail
    assert "P%40ss%20w0rd%21" not in detail
    assert "***:***@" in detail


# ---------- 音频分支 ----------


def test_audio_missing_track_is_anomaly_when_configured(tmp_path, monkeypatch):
    n = _client(tmp_path, monkeypatch, audio="nostream")
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [_rec("101", audio=True)]
    n._run_deep_av_checks(recs)
    assert recs[0]["视频抽检"] == "正常"
    assert recs[0]["音频抽检"] == "异常"
    assert "无音轨" in recs[0]["抽检详情"]


def test_audio_missing_track_is_skip_when_not_configured(tmp_path, monkeypatch):
    n = _client(tmp_path, monkeypatch, audio="nostream")
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [_rec("101", audio=False)]
    n._run_deep_av_checks(recs)
    assert recs[0]["视频抽检"] == "正常"
    assert recs[0]["音频抽检"] == "跳过"


def test_audio_silence_is_warning(tmp_path, monkeypatch):
    """mean_volume 低于 silence_db(-80) → 疑似静音，只警告不判异常。"""
    n = _client(tmp_path, monkeypatch, mean_db="-95.0")
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [_rec("101")]
    n._run_deep_av_checks(recs)
    assert recs[0]["音频抽检"] == "警告"
    assert "静音" in recs[0]["抽检详情"]


def test_audio_pull_failure_is_unknown_not_anomaly(tmp_path, monkeypatch):
    """拉流超时属瞬时失败：音频记「未知」而不是「异常」，避免误判。"""
    n = _client(tmp_path, monkeypatch, audio="fail")
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [_rec("101")]
    n._run_deep_av_checks(recs)
    assert recs[0]["视频抽检"] == "正常"
    assert recs[0]["音频抽检"] == "未知"


# ---------- 编排：候选筛选 / 上限 / 复检 / 取消 ----------


def test_deep_checks_filters_candidates_and_applies_limit(tmp_path, monkeypatch):
    n = _client(tmp_path, monkeypatch)
    n.av_limit = 2
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [
        _rec("101", ch="1"),
        _rec("201", ch="2"),
        _rec("301", ch="3"),
        _rec("401", ch="4", enabled=False),
        _rec("501", ch="5", disk="异常"),
    ]

    n._run_deep_av_checks(recs)

    by_ch = {r["通道"]: r for r in recs}
    # 未配置录像 / 落盘异常 → 不拉流，且写明理由
    assert by_ch["4"]["抽检详情"] == "未配置录像"
    assert by_ch["5"]["抽检详情"] == "近期无录像/未知,跳过拉流"
    assert by_ch["4"]["视频抽检"] == "跳过"
    assert by_ch["5"]["视频抽检"] == "跳过"
    # 上限之外的不抽
    assert by_ch["3"]["抽检详情"] == "超出抽检路数上限(2)"
    # 前两路真的抽了
    assert by_ch["1"]["视频抽检"] == "正常"
    assert by_ch["2"]["视频抽检"] == "正常"


def test_deep_checks_retry_recovers_and_annotates(tmp_path, monkeypatch):
    """初检失败 → 换时段复检通过 → 结果被覆盖并标注「复检通过」。"""
    n = _client(tmp_path, monkeypatch)
    calls: List[int] = []

    def fake_search(tid, s, e):
        calls.append(1)
        # 第一次给一个必然拉不动的 URI，复检时给正常的
        return _found(tid, host="FAILME" if len(calls) == 1 else "192.168.1.64")

    n._search_track_in_range = fake_search  # type: ignore
    recs = [_rec("101", name="前门")]

    n._run_deep_av_checks(recs)

    r = recs[0]
    assert len(calls) == 2, "应当发生一次复检"
    assert r["视频抽检"] == "正常", "复检通过后应覆盖为正常"
    assert "复检通过,初检失败" in r["抽检详情"]


def test_deep_checks_retry_still_failing_is_annotated(tmp_path, monkeypatch):
    """两次都失败 → 保留异常并标注复检时间点，不能悄悄变成正常。"""
    n = _client(tmp_path, monkeypatch)
    n._search_track_in_range = lambda tid, s, e: _found(tid, host="FAILME")  # type: ignore
    recs = [_rec("101", name="前门")]

    n._run_deep_av_checks(recs)

    r = recs[0]
    assert r["视频抽检"] == "异常"
    assert "复检@" in r["抽检详情"]
    assert "仍异常" in r["抽检详情"]


def test_deep_checks_propagates_cancellation(tmp_path, monkeypatch):
    """取消必须向上抛，不能被结果兜底吞掉（否则点了取消还在跑）。"""
    n = _client(tmp_path, monkeypatch)
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    n._cancelled.set()
    with pytest.raises(ScanCancelled):
        n._run_deep_av_checks([_rec("101")])


def test_probe_channel_at_skips_without_uri(tmp_path, monkeypatch):
    n = _client(tmp_path, monkeypatch)
    n._search_track_in_range = lambda tid, s, e: {  # type: ignore
        "ok": False, "playback_uri": None, "seg_start": None,
        "seg_end": None, "detail": "繁忙时段无录像片段",
    }
    rec = _rec("101")
    tid, res = n._probe_channel_at(
        rec, CLIP_S, CLIP_S + timedelta(seconds=3), "测试窗; 抽检点 09-10 14:00:00"
    )
    assert tid == "101"
    assert res["视频抽检"] == "跳过"
    assert "无录像片段" in res["抽检详情"]
    assert not _log_lines(n), "没有回放 URI 就不该调用 ffmpeg"


# ---------- 抽检通道过滤（--av-channels） ----------


def test_av_channels_limits_probing_to_listed_channels(tmp_path, monkeypatch):
    """只抽 --av-channels 列出的通道，其余一律跳过并写明理由。"""
    n = _client(tmp_path, monkeypatch, av_channels="31,64")
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [
        _rec("3101", ch="31", name="前端相机-1"),
        _rec("6401", ch="64", name="前端相机-2"),
        _rec("6501", ch="65"),
    ]

    n._run_deep_av_checks(recs)

    by_ch = {r["通道"]: r for r in recs}
    assert by_ch["31"]["视频抽检"] == "正常"
    assert by_ch["64"]["视频抽检"] == "正常"
    assert by_ch["65"]["视频抽检"] == "跳过"
    assert by_ch["65"]["音频抽检"] == "跳过"
    assert by_ch["65"]["抽检详情"] == "不在指定抽检通道列表"

    probed_tracks = " ".join(_log_lines(n))
    assert "/Streaming/tracks/3101/" in probed_tracks
    assert "/Streaming/tracks/6401/" in probed_tracks
    assert "/Streaming/tracks/6501/" not in probed_tracks, "列表外的通道不该被拉流"


def test_av_channels_applies_before_av_limit(tmp_path, monkeypatch):
    """`--av-channels 31,64 --av-limit 1` = 在 31/64 里最多抽 1 路。"""
    n = _client(tmp_path, monkeypatch, av_channels="31,64")
    n.av_limit = 1
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [
        _rec("3101", ch="31"),
        _rec("6401", ch="64"),
        _rec("6501", ch="65"),
    ]

    n._run_deep_av_checks(recs)

    by_ch = {r["通道"]: r for r in recs}
    assert by_ch["31"]["视频抽检"] == "正常"
    # 上限命中的那路写「上限」；列表外的写「不在列表」——两种原因不应混淆
    assert by_ch["64"]["抽检详情"] == "超出抽检路数上限(1)"
    assert by_ch["65"]["抽检详情"] == "不在指定抽检通道列表"


def test_av_channels_keeps_real_problem_reason_over_filter_reason(tmp_path, monkeypatch):
    """列表内的通道若「未配置录像」，仍要报真正的问题，别被过滤理由掩盖。"""
    n = _client(tmp_path, monkeypatch, av_channels="31,64")
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [
        _rec("3101", ch="31", enabled=False),
        _rec("6401", ch="64", disk="异常"),
    ]

    n._run_deep_av_checks(recs)

    by_ch = {r["通道"]: r for r in recs}
    assert by_ch["31"]["抽检详情"] == "未配置录像"
    assert by_ch["64"]["抽检详情"] == "近期无录像/未知,跳过拉流"
    assert not _log_lines(n), "无可抽通道时不该调用 ffmpeg"


def test_av_channels_none_probes_everything(tmp_path, monkeypatch):
    """不传 --av-channels 时行为不变：全部候选通道都抽。"""
    n = _client(tmp_path, monkeypatch)
    assert n.av_channels is None
    n._search_track_in_range = lambda tid, s, e: _found(tid)  # type: ignore
    recs = [_rec("3101", ch="31"), _rec("6401", ch="64")]

    n._run_deep_av_checks(recs)

    assert [r["视频抽检"] for r in recs] == ["正常", "正常"]


# ---------- 定点抽检（--av-at，多时刻） ----------


def _run_fixed(n, recs, canned, instants):
    """定点模式驱动器：_run_av_jobs 按窗口起点的「时刻」回放预制结果。

    canned 的键是 datetime（抽检窗起点）；instants 与 canned 键一一对应。
    """
    def fake_jobs(recs, s, e, label, lo, hi, text):
        assert s in canned, f"窗口起点 {s} 不在预置时刻里"
        # 与真实 _run_av_jobs 契约一致：结果按 id(rec) 建键
        # （track_id 可能重复/为「未知」，按它建键会串到别的通道上）
        return {id(r): dict(canned[s]) for r in recs}

    n.av_at = list(instants)
    n._run_av_jobs = fake_jobs  # type: ignore[method-assign]
    n._run_deep_av_checks(recs)


def test_av_at_multi_instant_uses_best_definite_and_records_trail(tmp_path, monkeypatch):
    """t1 未知 + t2 正常 → 结论取正常，详情保留多时刻轨迹（间歇故障恢复）。"""
    t1 = CLIP_S
    t2 = CLIP_S + timedelta(hours=2)
    n = _client(tmp_path, monkeypatch)
    canned = {
        t1: {"视频抽检": "未知", "音频抽检": "未知", "抽检详情": "拉流超时"},
        t2: {"视频抽检": "正常", "音频抽检": "正常", "抽检详情": ""},
    }
    recs = [_rec("3101", ch="31")]

    _run_fixed(n, recs, canned, [t1, t2])

    r = recs[0]
    assert r["视频抽检"] == "正常"
    assert r["音频抽检"] == "正常"
    assert "多时刻" in r["抽检详情"]
    # 轨迹按时刻顺序、两个时刻的结论都在
    t1_note = t1.astimezone(TZ_CN).strftime("%H:%M")
    t2_note = t2.astimezone(TZ_CN).strftime("%H:%M")
    assert t1_note in r["抽检详情"] and t2_note in r["抽检详情"]
    idx1, idx2 = r["抽检详情"].index(t1_note), r["抽检详情"].index(t2_note)
    assert idx1 < idx2, "轨迹应按时间先后排列"


def test_av_at_unknown_must_not_mask_confirmed_failure(tmp_path, monkeypatch):
    """t1 异常 + t2 未知 → 结论保持异常。缺证据(未知)不能掩盖已确认的故障。"""
    t1 = CLIP_S
    t2 = CLIP_S + timedelta(hours=2)
    n = _client(tmp_path, monkeypatch)
    canned = {
        t1: {"视频抽检": "异常", "音频抽检": "异常", "抽检详情": "连接失败"},
        t2: {"视频抽检": "未知", "音频抽检": "未知", "抽检详情": "拉流超时"},
    }
    recs = [_rec("3101", ch="31")]

    _run_fixed(n, recs, canned, [t1, t2])

    r = recs[0]
    assert r["视频抽检"] == "异常", "未知只是缺证据，不该顶掉已确认的异常"
    assert "多时刻" in r["抽检详情"]
    # 轨迹里保留各时刻结论(含未知那次的「未知/未知」),不静默
    t2_note = t2.astimezone(TZ_CN).strftime("%H:%M") + "=未知/未知"
    assert t2_note in r["抽检详情"]


def test_av_at_single_instant_keeps_plain_detail(tmp_path, monkeypatch):
    """单时刻定点：行为与常规抽检一致，详情不加「多时刻」前缀。"""
    t1 = CLIP_S
    n = _client(tmp_path, monkeypatch)
    canned = {
        t1: {"视频抽检": "正常", "音频抽检": "正常", "抽检详情": ""},
    }
    recs = [_rec("3101", ch="31")]

    _run_fixed(n, recs, canned, [t1])

    r = recs[0]
    assert r["视频抽检"] == "正常"
    assert "多时刻" not in (r["抽检详情"] or "")


def test_av_at_skips_auto_retry(tmp_path, monkeypatch):
    """定点模式不自动换时段复检；补测手段是追加 --av-at 时刻。"""
    t1 = CLIP_S
    n = _client(tmp_path, monkeypatch)
    canned = {
        t1: {"视频抽检": "异常", "音频抽检": "异常", "抽检详情": "连接失败"},
    }
    recs = [_rec("3101", ch="31")]

    def _no_retry(*a, **k):
        raise AssertionError("定点模式不应触发换时段复检")

    n._pick_retry_clip_times = _no_retry  # type: ignore[method-assign]
    _run_fixed(n, recs, canned, [t1])

    assert recs[0]["视频抽检"] == "异常"


# ---------- 回归：track_id 重复 / 为「未知」时结果不能串台 ----------


def _dup_recs() -> List[Dict]:
    """两路 track_id 完全相同：模拟 Track 缺 id/Channel 时都落成「未知」。"""
    return [_rec("未知", ch="31"), _rec("未知", ch="64")]


def test_duplicate_track_id_results_do_not_collide(tmp_path, monkeypatch):
    """重复 track_id 时每路必须拿到自己那份结论，不能被后完成的覆盖。

    回归点：`_run_av_jobs` 结果曾按 `str(track_id)` 建键。重复键下后一个 future
    会覆盖前一个，于是一路被写上另一路的抽检结论（且是静默的——两路都"看起来有结果"）。
    """
    n = _client(tmp_path, monkeypatch)  # av_workers=1

    def fake_probe(rec, s, e, label):
        return str(rec["track_id"]), {
            "视频抽检": "正常",
            "音频抽检": "正常",
            "抽检详情": f"CH{rec['通道']}-PROBE",
            "抽检时段": label,
            "保存路径": None,
        }

    n._probe_channel_at = fake_probe  # type: ignore[method-assign]
    recs = _dup_recs()

    n._run_deep_av_checks(recs)

    by_ch = {r["通道"]: r for r in recs}
    assert by_ch["31"]["抽检详情"] == "CH31-PROBE"
    assert by_ch["64"]["抽检详情"] == "CH64-PROBE"


def test_duplicate_track_id_keeps_separate_at_trails(tmp_path, monkeypatch):
    """定点多时刻模式下，重复 track_id 的两路各自累积轨迹，不合并成同一条。"""
    t1, t2 = CLIP_S, CLIP_S + timedelta(hours=2)
    n = _client(tmp_path, monkeypatch)

    def fake_probe(rec, s, e, label):
        ch = rec["通道"]
        # 两路的结论都是正常（不触发复检），但详情里各自留可区分的标记
        return str(rec["track_id"]), {
            "视频抽检": "正常",
            "音频抽检": "正常",
            "抽检详情": f"CH{ch}@{s.astimezone(TZ_CN).strftime('%H:%M')}",
            "抽检时段": label,
            "保存路径": None,
        }

    n._probe_channel_at = fake_probe  # type: ignore[method-assign]
    n.av_at = [t1, t2]
    recs = _dup_recs()

    n._run_deep_av_checks(recs)

    by_ch = {r["通道"]: r for r in recs}
    for ch in ("31", "64"):
        detail = by_ch[ch]["抽检详情"] or ""
        assert f"CH{ch}@" in detail, f"ch{ch} 没拿到自己的结论: {detail}"
        assert "多时刻" in detail, f"ch{ch} 应记录多时刻轨迹: {detail}"
    # 串台的话两路详情会一模一样
    assert by_ch["31"]["抽检详情"] != by_ch["64"]["抽检详情"]
