"""A4-5 取消巡检契约 + 信号节流批量器。"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

from hikvision_status import HikvisionNVR, ScanCancelled
from ui.scan_worker import ScanWorker, _SignalThrottle


def _tracks_root(tid: str = "101") -> ET.Element:
    """单 Track、已配置录像的最小 /ContentMgmt/record/tracks 响应。"""
    return ET.fromstring(
        f"""<TrackList><Track>
  <id>{tid}</id>
  <DefaultRecordingMode>CMR</DefaultRecordingMode>
  <Actions>
    <ActionRecordingMode>CMR</ActionRecordingMode>
    <Record>true</Record>
  </Actions>
</Track></TrackList>"""
    )


def test_disk_search_cancel_propagates_not_nameerror():
    """落盘检索阶段取消必须抛 ScanCancelled。

    回归：`except ScanCancelled:` 所引用的名字没有 import，
    取消一触发就先炸 NameError（把真正的取消异常吞掉）。
    注意：取消标志必须在**线程池已开跑之后**才置位 —— `_progress` 本身
    就是取消检查点，若提前置位，异常会从池外的 `_progress` 抛出，
    根本走不进 `except ScanCancelled:`，测试就成了假绿。
    """
    nvr = HikvisionNVR(
        ip="127.0.0.1", port=80, username="a", password="b", quiet=True
    )
    nvr._parse = lambda endpoint, tag="": _tracks_root()  # type: ignore[method-assign]
    nvr.get_cameras = lambda: []  # type: ignore[method-assign]

    def _cancel_mid_pool(track_id, lookback_minutes):
        nvr._cancelled.set()  # 模拟用户在落盘检索运行中点了取消
        raise ScanCancelled("巡检已取消")

    nvr._search_track_recent = _cancel_mid_pool  # type: ignore[method-assign]
    with pytest.raises(ScanCancelled):
        nvr.get_recording_status()


def test_nvr_cancel_raises_scan_cancelled():
    nvr = HikvisionNVR(
        ip="127.0.0.1", port=80, username="a", password="b", quiet=True
    )
    assert not nvr._cancelled.is_set()
    nvr.cancel()
    assert nvr._cancelled.is_set()
    with pytest.raises(ScanCancelled):
        nvr._check_cancel()


def test_worker_cancel_progress_raises():
    w = ScanWorker({"ip": "127.0.0.1", "name": "n"}, {})
    try:
        w.cancel()
        assert w.cancelled
        with pytest.raises(ScanCancelled):
            w._progress("x")
    finally:
        w._throttle.close()


def _drain_events(qapp, seconds=0.5):
    """跨线程 emit 是队列投递，需主线程转事件循环才能送达。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_worker_cancel_before_start_emits_cancelled(qapp):
    """取消置位后启动：不得 emit scan_finished，只 emit scan_cancelled（无网络）。"""
    w = ScanWorker({"ip": "127.0.0.1", "name": "n"}, {"no_search": True})
    events = []
    w.scan_cancelled.connect(lambda: events.append("cancelled"))
    w.scan_finished.connect(lambda _d: events.append("finished"))
    w.scan_failed.connect(lambda _e: events.append("failed"))
    w.cancel()
    w.start()
    w.join(timeout=10)
    w._throttle.close()
    _drain_events(qapp)
    assert events == ["cancelled"]


def test_signal_throttle_batches_and_keeps_latest_progress():
    logs, progs = [], []
    t = _SignalThrottle(logs.append, progs.append, interval=0.03)
    try:
        for i in range(5):
            t.add_log(f"m{i}")
            t.add_progress({"i": i})
        t.flush()
        assert logs == ["m0", "m1", "m2", "m3", "m4"]  # 日志逐条保留
        assert progs == [{"i": 4}]                       # 进度只保留最新一帧
        # 合并线程随后周期 flush，close 安全退出
    finally:
        t.close()
    assert not t._thread.is_alive()
