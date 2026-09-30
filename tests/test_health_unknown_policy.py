"""「未知」健康口径回归（落盘未知 / 音频抽检未知）。

原口径：「落盘未知」与「音频抽检未知」都无条件 `raise_to("警告")`。
两者成因多为**瞬时**检索/拉流超时，任一路抖动即把整机降级，告警偏吵，
且两套「未知」口径若只调一个就会不一致。

新口径（两个一起调）：
  · 实际检查到的通道里**只有部分未知** → 仅进统计与结果区，不参与健康判定；
  · 实际检查到的通道**全部未知**（整体检索 / 拉流失败）→ 升级为警告。

本文件同时守住「两个口径一致」这一约束。
"""

from __future__ import annotations

from typing import Dict, List

from nvr_core.health import HealthMixin


class _Stub(HealthMixin):
    def __init__(
        self,
        records: List[Dict],
        cameras: List[Dict] | None = None,
        deep: bool = False,
        disk_checked: bool = True,
    ):
        self._records = records
        self._cameras = cameras or []
        self.deep_av_check = deep
        self.check_disk_recording = disk_checked

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


def _rec(ch, disk="正常", audio_probe=None, video=None) -> Dict:
    """一条录像记录；默认「计划已配 + 录像正常 + 含音频」，只让被测项变化。"""
    return {
        "通道": str(ch),
        "名称": f"通道{ch}",
        "已启用录像": True,
        "录像是否正常": "正常",
        "录像含音频": True,
        "落盘状态": disk,
        "视频抽检": video,
        "音频抽检": audio_probe,
    }


# ── 落盘未知 ──────────────────────────────────────────────────


def test_partial_disk_unknown_does_not_degrade_health():
    """1 路瞬时超时不应把整机降级（本次改动的核心诉求）。"""
    records = [_rec(1, disk="正常"), _rec(2, disk="正常"), _rec(3, disk="未知")]
    h = _Stub(records).get_health_summary()
    assert h["健康状态"] == "良好"
    assert h["统计"]["落盘未知"] == 1
    assert not any("未知" in w and "录像" in w for w in h["预警信息"])


def test_all_disk_unknown_escalates_to_warning():
    """整体检索失败（全部未知）仍须预警，避免变成假阴性。"""
    records = [_rec(1, disk="未知"), _rec(2, disk="未知")]
    h = _Stub(records).get_health_summary()
    assert h["健康状态"] == "警告"
    assert any("整体检索失败" in w for w in h["预警信息"])


def test_disk_unknown_does_not_warn_when_not_checked():
    """未实际检索落盘时，「落盘未知」不参与判定。"""
    records = [_rec(1, disk="未知")]
    h = _Stub(records, disk_checked=False, deep=False).get_health_summary()
    assert h["健康状态"] == "良好"
    assert not any("整体检索失败" in w for w in h["预警信息"])


def test_disk_all_ok_stays_healthy():
    records = [_rec(1), _rec(2)]
    h = _Stub(records).get_health_summary()
    assert h["健康状态"] == "良好"
    assert h["统计"]["落盘未知"] == 0


# ── 音频抽检未知（深度模式） ──────────────────────────────────


def test_partial_audio_unknown_does_not_degrade_health():
    records = [
        _rec(1, audio_probe="正常", video="正常"),
        _rec(2, audio_probe="未知", video="正常"),
    ]
    h = _Stub(records, deep=True).get_health_summary()
    assert h["健康状态"] == "良好"
    assert h["统计"]["音频抽检未知"] == 1


def test_all_audio_unknown_escalates_to_warning():
    records = [
        _rec(1, audio_probe="未知", video="正常"),
        _rec(2, audio_probe="未知", video="正常"),
    ]
    h = _Stub(records, deep=True).get_health_summary()
    assert h["健康状态"] == "警告"
    assert any("整体拉流失败" in w for w in h["预警信息"])


def test_audio_unknown_ignored_when_skipped_channels_exist():
    """「跳过」（未开音频）不计入分母：只有真正抽样的通道全未知才算整体失败。"""
    records = [
        _rec(1, audio_probe="正常", video="正常"),
        _rec(2, audio_probe="跳过", video="正常"),
        _rec(3, audio_probe="未知", video="正常"),
    ]
    h = _Stub(records, deep=True).get_health_summary()
    # 抽样 2 路，1 正常 1 未知 → 部分未知 → 不降级
    assert h["健康状态"] == "良好"


def test_audio_unknown_not_judged_in_non_deep_mode():
    """非深度模式不产出音频抽检字段，口径不适用。"""
    records = [_rec(1)]
    h = _Stub(records, deep=False).get_health_summary()
    assert h["健康状态"] == "良好"
    assert h["统计"]["音频抽检未知"] == 0


# ── 两个口径一致 ─────────────────────────────────────────────


def test_both_unknown_columns_share_the_same_policy():
    """两个「未知」都在「部分未知」时保持良好、在「全部未知」时升警告。"""
    partial = _Stub(
        [
            _rec(1, disk="正常", audio_probe="正常", video="正常"),
            _rec(2, disk="未知", audio_probe="未知", video="正常"),
        ],
        deep=True,
    ).get_health_summary()
    assert partial["健康状态"] == "良好"

    full = _Stub(
        [
            _rec(1, disk="未知", audio_probe="未知", video="正常"),
            _rec(2, disk="未知", audio_probe="未知", video="正常"),
        ],
        deep=True,
    ).get_health_summary()
    assert full["健康状态"] == "警告"
