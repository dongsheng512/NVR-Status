"""`chanDetectResult`(检测状态) 纳入健康判定的回归。

背景：`isapi_client._get_input_proxy_cameras` 早就把
`InputProxy/channels/status` 的 `chanDetectResult` 采进了 `检测状态`，
但全仓零使用（只赋值、不判定）。本文件守住新口径：

  · online=false            → 离线
  · online=true             → 在线；检测状态明确异常时另记一条「通道检测异常」
  · online 缺失/取值异常     → 用检测状态补判（connect→在线，异常→离线）
  · 两者都非明确取值         → 未确认（不误报离线）

采用白名单：只认明确正常/异常的取值，其余一律不参与判定（宁可漏判不误报）。
"""

from __future__ import annotations

from typing import Dict, List

import pytest

from nvr_core.health import HealthMixin, classify_detect_state, detect_reason


class _Stub(HealthMixin):
    """只提供 HealthMixin 需要的数据源，不建 Session、不联网。"""

    def __init__(self, cameras: List[Dict], records: List[Dict] | None = None):
        self._cameras = cameras
        self._records = records or []
        self.check_disk_recording = True
        self.deep_av_check = False

    def get_system_status(self) -> Dict:
        return {}

    def get_storage_status(self) -> List[Dict]:
        return []

    def get_disk_overwrite_status(self, drives=None) -> Dict:
        return {"enabled": True, "label": "已开启"}

    def get_cameras(self) -> List[Dict]:
        return self._cameras

    def get_recording_status(self) -> List[Dict]:
        return self._records


def _cam(online, detect, ch=1, name=None) -> Dict:
    return {
        "id": str(ch),
        "名称": name or f"通道{ch}",
        "IP": "192.168.1.10",
        "型号": "DS-2CD",
        "在线": online,
        "检测状态": detect,
    }


# ── 归一化函数单测 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("connect", "在线"),
        ("Connect", "在线"),
        ("connected", "在线"),
        ("已配置", "在线"),
        ("notExist", "异常"),
        ("NOTEXIST", "异常"),
        ("ipConflict", "异常"),
        ("netError", "异常"),
        ("unknown", ""),          # 非明确取值 → 不判定
        ("", ""),
        (None, ""),
        ("某个没见过的新取值", ""),  # 白名单外 → 不判定（不误报）
    ],
)
def test_classify_detect_state_whitelist(raw, expected):
    assert classify_detect_state(raw) == expected


def test_detect_reason_maps_known_and_falls_back():
    assert detect_reason("notExist") == "通道未接入"
    assert detect_reason("netError") == "网络不可达"
    assert detect_reason("weirdValue") == "weirdValue"


# ── 在线 / 离线 判定 ──────────────────────────────────────────


def test_online_missing_detect_notexist_counts_offline():
    """online 缺失时，用 notExist 补判为离线并升级严重。"""
    n = _Stub([_cam("", "notExist", ch=1, name="前门")])
    h = n.get_health_summary()
    stats = h["统计"]
    assert h["健康状态"] == "严重"
    assert stats["摄像头离线"] == 1
    assert stats["摄像头在线"] == 0
    assert stats["摄像头状态未确认"] == 0
    assert any("离线" in w for w in h["预警信息"])


def test_online_missing_detect_connect_counts_online():
    """online 缺失时，用 connect 补判为在线，不应误报离线。"""
    n = _Stub([_cam("", "connect")])
    h = n.get_health_summary()
    assert h["健康状态"] == "良好"
    assert h["统计"]["摄像头在线"] == 1
    assert h["统计"]["摄像头离线"] == 0


def test_unknown_detect_value_is_not_judged():
    """白名单外的取值既不判在线也不判离线，只记「未确认」，避免误报。"""
    n = _Stub([_cam("", "someNewFirmwareValue")])
    h = n.get_health_summary()
    assert h["健康状态"] == "良好"
    assert h["统计"]["摄像头在线"] == 0
    assert h["统计"]["摄像头离线"] == 0
    assert h["统计"]["摄像头状态未确认"] == 1
    assert not h["预警信息"]


def test_online_false_takes_precedence():
    """online=false 是权威判据：即便检测状态说 connect，也判离线。"""
    n = _Stub([_cam("false", "connect")])
    h = n.get_health_summary()
    assert h["健康状态"] == "严重"
    assert h["统计"]["摄像头离线"] == 1


def test_online_false_counts_even_without_detect():
    n = _Stub([_cam("false", "unknown")])
    h = n.get_health_summary()
    assert h["统计"]["摄像头离线"] == 1


def test_online_true_with_abnormal_detect_adds_warning():
    """在线但通道级检测异常（如网络不可达）应另立一条警告，不混入离线。"""
    n = _Stub([_cam("true", "netError", ch=1, name="后门")])
    h = n.get_health_summary()
    stats = h["统计"]
    assert h["健康状态"] == "警告"
    assert stats["摄像头在线"] == 1
    assert stats["摄像头离线"] == 0
    assert stats["通道检测异常"] == 1
    assert any("通道检测状态异常" in w for w in h["预警信息"])


def test_online_true_with_normal_detect_no_warning():
    n = _Stub([_cam("true", "connect")])
    h = n.get_health_summary()
    assert h["健康状态"] == "良好"
    assert h["统计"]["通道检测异常"] == 0
    assert not h["预警信息"]


def test_offline_warning_breaks_down_reasons():
    """离线预警应尽量给出原因细分（未接入 / 网络不可达）。"""
    n = _Stub([
        _cam("false", "notExist", ch=1, name="通道1"),
        _cam("false", "notExist", ch=2, name="通道2"),
        _cam("false", "netError", ch=3, name="通道3"),
    ])
    h = n.get_health_summary()
    msg = " ".join(h["预警信息"])
    assert "3个摄像头离线" in msg
    assert "通道未接入 2" in msg
    assert "网络不可达 1" in msg


def test_offline_without_detect_falls_back_to_names():
    """检测状态给不出原因时，退回原来的「(名称)」展示。"""
    n = _Stub([_cam("false", "unknown", ch=1, name="前门")])
    h = n.get_health_summary()
    assert any("前门" in w for w in h["预警信息"])


def test_detect_state_alone_does_not_invent_cameras():
    """总数/未确认统计自洽：在线+离线+未确认 == 摄像头总数。"""
    cams = [
        _cam("true", "connect", ch=1),
        _cam("false", "notExist", ch=2),
        _cam("", "connect", ch=3),
        _cam("", "notExist", ch=4),
        _cam("", "mystery", ch=5),
    ]
    n = _Stub(cams)
    stats = n.get_health_summary()["统计"]
    assert stats["摄像头总数"] == 5
    assert stats["摄像头在线"] == 2       # ch1 online + ch3 detect
    assert stats["摄像头离线"] == 2       # ch2 online=false + ch4 detect
    assert stats["摄像头状态未确认"] == 1  # ch5
    assert (
        stats["摄像头在线"] + stats["摄像头离线"] + stats["摄像头状态未确认"]
        == stats["摄像头总数"]
    )
